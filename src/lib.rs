// lib.rs: Claude Dashboard WebAssembly module.
// Receives CBOR data bytes from JS, renders the dashboard HTML.

use wasm_bindgen::prelude::*;
use serde::Deserialize;
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// Data model (mirrors generate-data.py output)
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct DashboardData {
  generated_at: ciborium::tag::Required<String, 1>,
  projects: Vec<Project>,
}

#[derive(Debug, Deserialize)]
struct Project {
  name: String,
  path: String,
  runs: Vec<Run>,
}

#[derive(Debug, Deserialize)]
struct Run {
  // CBOR timestamps come as tagged items; we accept as optional strings
  #[serde(default)]
  start: Option<String>,
  #[serde(default)]
  end: Option<String>,
  #[serde(default)]
  invoked: bool,
  #[serde(default)]
  cost_usd: Option<f64>,
  #[serde(default)]
  tokens_in: Option<u64>,
  #[serde(default)]
  tokens_out: Option<u64>,
  #[serde(default)]
  log: String,
}

// ---------------------------------------------------------------------------
// CBOR deserialisation helpers
// ---------------------------------------------------------------------------

// ciborium represents timestamps as tagged values; we map them to strings.
// This custom visitor accepts either a string or a tagged datetime.
fn decode_data(bytes: &[u8]) -> Result<(String, Vec<ProjectView>), String> {
  // Use ciborium to parse into a generic Value first, then extract fields.
  let value: ciborium::value::Value = ciborium::de::from_reader(bytes)
    .map_err(|e| format!("CBOR parse error: {e}"))?;

  let map = match value {
    ciborium::value::Value::Map(m) => m,
    _ => return Err("top-level value is not a map".into()),
  };

  let mut top: BTreeMap<String, ciborium::value::Value> = BTreeMap::new();
  for (k, v) in map {
    if let ciborium::value::Value::Text(key) = k {
      top.insert(key, v);
    }
  }

  let generated_at = extract_text_or_tag(&top, "generated_at")
    .unwrap_or_default();

  let projects = match top.get("projects") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_project).collect(),
    _ => vec![],
  };

  Ok((generated_at, projects))
}

fn extract_text_or_tag(
  map: &BTreeMap<String, ciborium::value::Value>,
  key: &str,
) -> Option<String> {
  match map.get(key)? {
    ciborium::value::Value::Text(s) => Some(s.clone()),
    ciborium::value::Value::Tag(_, inner) => {
      if let ciborium::value::Value::Text(s) = inner.as_ref() {
        Some(s.clone())
      } else {
        None
      }
    }
    _ => None,
  }
}

fn val_as_map(v: &ciborium::value::Value) -> BTreeMap<String, ciborium::value::Value> {
  let mut out = BTreeMap::new();
  if let ciborium::value::Value::Map(pairs) = v {
    for (k, val) in pairs {
      if let ciborium::value::Value::Text(key) = k {
        out.insert(key.clone(), val.clone());
      }
    }
  }
  out
}

fn val_as_bool(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> bool {
  matches!(map.get(key), Some(ciborium::value::Value::Bool(true)))
}

fn val_as_f64(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> Option<f64> {
  match map.get(key)? {
    ciborium::value::Value::Float(f) => Some(*f),
    ciborium::value::Value::Integer(i) => Some((*i).into()),
    _ => None,
  }
}

fn val_as_u64(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> Option<u64> {
  match map.get(key)? {
    ciborium::value::Value::Integer(i) => {
      let n: i128 = (*i).into();
      if n >= 0 { Some(n as u64) } else { None }
    }
    _ => None,
  }
}

fn val_as_str(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> String {
  match map.get(key) {
    Some(ciborium::value::Value::Text(s)) => s.clone(),
    _ => String::new(),
  }
}

// ---------------------------------------------------------------------------
// View structs for rendering
// ---------------------------------------------------------------------------

struct RunView {
  start: String,
  invoked: bool,
  duration: String,
  cost: String,
  log: String,
}

struct ProjectView {
  name: String,
  path: String,
  runs: Vec<RunView>,
}

fn parse_project(v: &ciborium::value::Value) -> ProjectView {
  let map = val_as_map(v);
  let name = val_as_str(&map, "name");
  let path = val_as_str(&map, "path");
  let runs = match map.get("runs") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_run).collect(),
    _ => vec![],
  };
  ProjectView { name, path, runs }
}

fn parse_run(v: &ciborium::value::Value) -> RunView {
  let map = val_as_map(v);
  let start = extract_text_or_tag(&map, "start").unwrap_or_default();
  let end_s = extract_text_or_tag(&map, "end");
  let invoked = val_as_bool(&map, "invoked");
  let cost_usd = val_as_f64(&map, "cost_usd");
  let tokens_in = val_as_u64(&map, "tokens_in");
  let tokens_out = val_as_u64(&map, "tokens_out");
  let log = val_as_str(&map, "log");

  let duration = if !start.is_empty() && end_s.is_some() {
    // Duration displayed by Python-side already; just show end time here
    end_s.unwrap_or_default()
  } else {
    String::from("—")
  };

  let cost = if let Some(c) = cost_usd {
    format!("${c:.4}")
  } else if tokens_in.is_some() || tokens_out.is_some() {
    let mut parts = vec![];
    if let Some(i) = tokens_in { parts.push(format!("{i} in")); }
    if let Some(o) = tokens_out { parts.push(format!("{o} out")); }
    parts.join(" / ")
  } else {
    String::from("—")
  };

  RunView { start, invoked, duration, cost, log }
}

// ---------------------------------------------------------------------------
// HTML rendering
// ---------------------------------------------------------------------------

fn esc(s: &str) -> String {
  s.replace('&', "&amp;")
   .replace('<', "&lt;")
   .replace('>', "&gt;")
   .replace('"', "&quot;")
}

fn render_run_row(run: &RunView) -> String {
  let row_class = if run.invoked { "table-warning" } else { "" };
  let invoked_label = if run.invoked { "Yes" } else { "No" };
  format!(
    r#"<tr class="{row_class}">
      <td>{start}</td>
      <td>{invoked}</td>
      <td>{dur}</td>
      <td>{cost}</td>
      <td><details><summary>show</summary><pre class="log-snippet">{log}</pre></details></td>
    </tr>"#,
    row_class = row_class,
    start = esc(&run.start),
    invoked = invoked_label,
    dur = esc(&run.duration),
    cost = esc(&run.cost),
    log = esc(&run.log),
  )
}

fn render_project(proj: &ProjectView) -> String {
  let rows: String = if proj.runs.is_empty() {
    r#"<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>"#.into()
  } else {
    proj.runs.iter().map(render_run_row).collect()
  };

  format!(
    r#"<section class="project-section mb-5" id="proj-{id}">
  <h2 class="h4">{name}</h2>
  <p class="text-muted small">{path}</p>
  <div class="table-responsive">
    <table class="table table-sm table-bordered table-hover align-middle">
      <thead class="table-dark">
        <tr>
          <th>Start (UTC)</th><th>Invoked</th><th>End (UTC)</th>
          <th>Cost / Tokens</th><th>Log</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>"#,
    id = esc(&proj.name),
    name = esc(&proj.name),
    path = esc(&proj.path),
    rows = rows,
  )
}

// ---------------------------------------------------------------------------
// Public WASM API
// ---------------------------------------------------------------------------

/// Render dashboard HTML from raw CBOR bytes.
/// Returns an HTML string on success, or an error message prefixed with "ERROR:".
#[wasm_bindgen]
pub fn render_dashboard(cbor_bytes: &[u8]) -> String {
  match decode_data(cbor_bytes) {
    Err(e) => format!("ERROR: {e}"),
    Ok((generated_at, projects)) => {
      let sections: String = projects.iter().map(render_project).collect();
      let nav: String = projects.iter().map(|p|
        format!(r#"<li class="nav-item"><a class="nav-link" href="#proj-{id}">{name}</a></li>"#,
          id = esc(&p.name), name = esc(&p.name))
      ).collect();
      format!(
        r#"<div id="dash-nav-items">{nav}</div>
<div id="dash-generated-at">{gen}</div>
<div id="dash-content">{sections}</div>"#,
        nav = nav,
        gen = esc(&generated_at),
        sections = sections,
      )
    }
  }
}

/// Return the version string.
#[wasm_bindgen]
pub fn version() -> String {
  env!("CARGO_PKG_VERSION").into()
}
