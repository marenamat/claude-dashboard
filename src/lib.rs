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
// Timestamp helpers
// ---------------------------------------------------------------------------

// Parse ISO 8601 datetime string "YYYY-MM-DDTHH:MM:SS[.frac][+HH:MM|Z]"
// into Unix epoch seconds.  Only handles UTC (+00:00 / Z) cleanly;
// other offsets are parsed and applied correctly too.
fn parse_timestamp_secs(s: &str) -> Option<i64> {
  let s = s.trim();
  if s.len() < 19 { return None; }

  // Parse fixed fields
  let y: i64  = s[0..4].parse().ok()?;
  let mo: i64 = s[5..7].parse().ok()?;
  let d: i64  = s[8..10].parse().ok()?;
  let h: i64  = s[11..13].parse().ok()?;
  let mi: i64 = s[14..16].parse().ok()?;
  let sc: i64 = s[17..19].parse().ok()?;

  // Parse timezone offset (everything after the fractional seconds)
  let tz_offset_secs: i64 = {
    // Skip fractional seconds
    let rest = &s[19..];
    let rest = if rest.starts_with('.') {
      // Find end of fractional part
      let end = rest.find(|c: char| !c.is_ascii_digit() && c != '.').unwrap_or(rest.len());
      &rest[end..]
    } else {
      rest
    };
    if rest.is_empty() || rest == "Z" {
      0
    } else if rest.starts_with('+') || rest.starts_with('-') {
      let sign: i64 = if rest.starts_with('-') { -1 } else { 1 };
      let tz = &rest[1..];
      let tz_h: i64 = tz.get(0..2).and_then(|v| v.parse().ok()).unwrap_or(0);
      let tz_m: i64 = tz.get(3..5).and_then(|v| v.parse().ok()).unwrap_or(0);
      sign * (tz_h * 3600 + tz_m * 60)
    } else {
      0
    }
  };

  // Days since Unix epoch using the civil-to-days algorithm
  // (https://howardhinnant.github.io/date_algorithms.html)
  let y = if mo <= 2 { y - 1 } else { y };
  let era: i64 = if y >= 0 { y } else { y - 399 } / 400;
  let yoe: i64 = y - era * 400;
  let doy: i64 = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + d - 1;
  let doe: i64 = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  let days: i64 = era * 146097 + doe - 719468;

  Some(days * 86400 + h * 3600 + mi * 60 + sc - tz_offset_secs)
}

// Format an ISO datetime string for display, stripping sub-second and timezone noise.
fn fmt_ts(s: &str) -> String {
  if s.len() >= 19 {
    // "YYYY-MM-DD HH:MM:SS UTC"
    format!("{} {} UTC", &s[0..10], &s[11..19])
  } else {
    s.to_owned()
  }
}

// Compute human-readable duration from two ISO timestamp strings.
fn compute_duration(start: &str, end: &str) -> String {
  let s0 = match parse_timestamp_secs(start) { Some(v) => v, None => return "—".into() };
  let s1 = match parse_timestamp_secs(end)   { Some(v) => v, None => return "—".into() };
  let secs = s1 - s0;
  if secs < 0 { return "—".into(); }
  let m = secs / 60;
  let s = secs % 60;
  if m > 0 { format!("{m}m {s}s") } else { format!("{s}s") }
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
  let start_raw = extract_text_or_tag(&map, "start").unwrap_or_default();
  let end_raw   = extract_text_or_tag(&map, "end").unwrap_or_default();
  let invoked   = val_as_bool(&map, "invoked");
  let cost_usd  = val_as_f64(&map, "cost_usd");
  let tokens_in  = val_as_u64(&map, "tokens_in");
  let tokens_out = val_as_u64(&map, "tokens_out");
  let log = val_as_str(&map, "log");

  let start    = if start_raw.is_empty() { "—".into() } else { fmt_ts(&start_raw) };
  let duration = if !start_raw.is_empty() && !end_raw.is_empty() {
    compute_duration(&start_raw, &end_raw)
  } else {
    "—".into()
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
          <th>Start (UTC)</th><th>Invoked</th><th>Duration</th>
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
