// lib.rs: Claude Dashboard WebAssembly module.
// Receives CBOR data bytes from JS, renders the dashboard HTML.

use wasm_bindgen::prelude::*;
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// CBOR deserialisation helpers
// ---------------------------------------------------------------------------

fn decode_data(bytes: &[u8]) -> Result<(String, ExchangeRates, Vec<ProjectView>), String> {
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

  // Exchange rates are optional; default to safe fallback values (issue #11)
  let rates = match top.get("exchange_rates") {
    Some(v) => {
      let rm = val_as_map(v);
      ExchangeRates {
        usd_to_eur: val_as_f64(&rm, "usd_to_eur").unwrap_or(0.92),
        usd_to_czk: val_as_f64(&rm, "usd_to_czk").unwrap_or(23.0),
      }
    }
    None => ExchangeRates { usd_to_eur: 0.92, usd_to_czk: 23.0 },
  };

  let projects = match top.get("projects") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_project).collect(),
    _ => vec![],
  };

  Ok((generated_at, rates, projects))
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
    ciborium::value::Value::Integer(i) => i64::try_from(*i).ok().map(|n| n as f64),
    _ => None,
  }
}

fn val_as_u64(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> Option<u64> {
  match map.get(key)? {
    ciborium::value::Value::Integer(i) => u64::try_from(*i).ok(),
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
// into Unix epoch seconds.
fn parse_timestamp_secs(s: &str) -> Option<i64> {
  let s = s.trim();
  if s.len() < 19 { return None; }

  let y: i64  = s[0..4].parse().ok()?;
  let mo: i64 = s[5..7].parse().ok()?;
  let d: i64  = s[8..10].parse().ok()?;
  let h: i64  = s[11..13].parse().ok()?;
  let mi: i64 = s[14..16].parse().ok()?;
  let sc: i64 = s[17..19].parse().ok()?;

  let tz_offset_secs: i64 = {
    let rest = &s[19..];
    let rest = if rest.starts_with('.') {
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

  // Days since Unix epoch (https://howardhinnant.github.io/date_algorithms.html)
  let y = if mo <= 2 { y - 1 } else { y };
  let era: i64 = if y >= 0 { y } else { y - 399 } / 400;
  let yoe: i64 = y - era * 400;
  let doy: i64 = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + d - 1;
  let doe: i64 = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  let days: i64 = era * 146097 + doe - 719468;

  Some(days * 86400 + h * 3600 + mi * 60 + sc - tz_offset_secs)
}

// Extract (year, month 1-12, day 1-31) from epoch seconds (UTC).
fn epoch_to_ymd(secs: i64) -> (i32, u32, u32) {
  let days = secs.div_euclid(86400);
  let z = days + 719468;
  let era = z.div_euclid(146097);
  let doe = z - era * 146097;
  let yoe = (doe - doe/1460 + doe/36524 - doe/146096) / 365;
  let y = yoe + era * 400;
  let doy = doe - (365*yoe + yoe/4 - yoe/100);
  let mp = (5*doy + 2)/153;
  let d = doy - (153*mp+2)/5 + 1;
  let m = if mp < 10 { mp + 3 } else { mp - 9 };
  let y = if m <= 2 { y + 1 } else { y };
  (y as i32, m as u32, d as u32)
}

// Weekday from epoch seconds (UTC): 0=Sunday … 6=Saturday.
// Unix epoch (1970-01-01) was a Thursday (4).
fn epoch_to_weekday(secs: i64) -> usize {
  let days = secs.div_euclid(86400);
  ((days + 4).rem_euclid(7)) as usize
}

// Format a run timestamp relative to now_secs in the browser's local timezone.
// tz_offset_secs: local UTC offset in seconds (positive = east of UTC), e.g. +3600 for UTC+1.
// - same local day  → "today HH:MM"
// - 1 day ago       → "yesterday HH:MM"
// - 2–5 days        → "weekday HH:MM"
// - older           → "Apr 01, HH:MM"
fn fmt_ts_relative(ts: &str, now_secs: i64, tz_offset_secs: i64) -> String {
  let ts_secs = match parse_timestamp_secs(ts) {
    Some(v) => v,
    // Fallback: can't parse; show raw date+time portion unchanged
    None => return if ts.len() >= 16 { format!("{} {}", &ts[0..10], &ts[11..16]) } else { ts.to_owned() },
  };

  // Shift both timestamps into local time for day boundary comparisons
  let local_ts  = ts_secs  + tz_offset_secs;
  let local_now = now_secs + tz_offset_secs;

  // HH:MM from local epoch seconds
  let day_secs = local_ts.rem_euclid(86400);
  let hh = day_secs / 3600;
  let mm = (day_secs % 3600) / 60;
  let hhmm = format!("{:02}:{:02}", hh, mm);

  let ts_day  = local_ts.div_euclid(86400);
  let now_day = local_now.div_euclid(86400);
  let days_ago = now_day - ts_day;

  const WEEKDAYS: [&str; 7] = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  const MONTHS:   [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  match days_ago {
    0      => format!("today {hhmm}"),
    1      => format!("yesterday {hhmm}"),
    2..=5  => {
      // Weekday of the local timestamp
      let wd = epoch_to_weekday(local_ts);
      format!("{} {hhmm}", WEEKDAYS[wd])
    }
    _ => {
      let (_, m, d) = epoch_to_ymd(local_ts);
      format!("{} {:02}, {hhmm}", MONTHS[(m - 1) as usize], d)
    }
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
  start_raw: String,  // raw ISO string for relative formatting
  invoked: bool,
  limit_hit: bool,
  duration: String,
  cost: String,
  log: String,
}

struct PrepView {
  decision: String,
  reasons:  Vec<String>,
}

// One time-bucket of token statistics (issue #11)
struct TokenBucket {
  tokens_in:  u64,
  tokens_out: u64,
  cost_usd:   f64,
}

struct TokenStats {
  day:  TokenBucket,
  week: TokenBucket,
  life: TokenBucket,
}

// Exchange rates fetched from ECB at build time (issue #11)
struct ExchangeRates {
  usd_to_eur: f64,
  usd_to_czk: f64,
}

struct ProjectView {
  name:        String,
  path:        String,
  runs:        Vec<RunView>,
  prep:        Option<PrepView>,
  token_stats: Option<TokenStats>,
}

fn parse_prep(v: &ciborium::value::Value) -> PrepView {
  let map = val_as_map(v);
  let decision = val_as_str(&map, "decision");
  let reasons = match map.get("reasons") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().filter_map(|item| {
      if let ciborium::value::Value::Text(s) = item { Some(s.clone()) } else { None }
    }).collect(),
    _ => vec![],
  };
  PrepView { decision, reasons }
}

fn parse_token_bucket(map: &BTreeMap<String, ciborium::value::Value>, key: &str) -> TokenBucket {
  let inner = match map.get(key) {
    Some(v) => val_as_map(v),
    None => BTreeMap::new(),
  };
  TokenBucket {
    tokens_in:  val_as_u64(&inner, "tokens_in").unwrap_or(0),
    tokens_out: val_as_u64(&inner, "tokens_out").unwrap_or(0),
    cost_usd:   val_as_f64(&inner, "cost_usd").unwrap_or(0.0),
  }
}

fn parse_token_stats(v: &ciborium::value::Value) -> TokenStats {
  let map = val_as_map(v);
  TokenStats {
    day:  parse_token_bucket(&map, "day"),
    week: parse_token_bucket(&map, "week"),
    life: parse_token_bucket(&map, "life"),
  }
}

fn parse_project(v: &ciborium::value::Value) -> ProjectView {
  let map = val_as_map(v);
  let name = val_as_str(&map, "name");
  let path = val_as_str(&map, "path");
  let runs = match map.get("runs") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_run).collect(),
    _ => vec![],
  };
  // prep is an optional map
  let prep = match map.get("prep") {
    Some(v @ ciborium::value::Value::Map(_)) => Some(parse_prep(v)),
    _ => None,
  };
  // token_stats is an optional map (issue #11)
  let token_stats = match map.get("token_stats") {
    Some(v @ ciborium::value::Value::Map(_)) => Some(parse_token_stats(v)),
    _ => None,
  };
  ProjectView { name, path, runs, prep, token_stats }
}

fn parse_run(v: &ciborium::value::Value) -> RunView {
  let map = val_as_map(v);
  let start_raw = extract_text_or_tag(&map, "start").unwrap_or_default();
  let end_raw   = extract_text_or_tag(&map, "end").unwrap_or_default();
  let invoked   = val_as_bool(&map, "invoked");
  let limit_hit = val_as_bool(&map, "limit_hit");
  let cost_usd  = val_as_f64(&map, "cost_usd");
  let tokens_in  = val_as_u64(&map, "tokens_in");
  let tokens_out = val_as_u64(&map, "tokens_out");
  let log = val_as_str(&map, "log");

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

  RunView { start_raw, invoked, limit_hit, duration, cost, log }
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

fn render_run_row(run: &RunView, now_secs: i64, tz_offset_secs: i64, hidden: bool) -> String {
  let start_disp = if run.start_raw.is_empty() {
    "—".to_owned()
  } else {
    fmt_ts_relative(&run.start_raw, now_secs, tz_offset_secs)
  };
  // Build row class: colour + optional hidden marker for JS progressive reveal
  let mut classes: Vec<&str> = Vec::new();
  if run.limit_hit      { classes.push("table-danger"); }
  else if run.invoked   { classes.push("table-warning"); }
  if hidden             { classes.push("run-hidden"); classes.push("d-none"); }
  let row_class = classes.join(" ");

  let inv_class = if run.invoked { "inv-dot inv-yes" } else { "inv-dot inv-no" };
  let limit_badge = if run.limit_hit {
    r#" <span class="badge bg-danger ms-1" title="Hit rate limit">limit</span>"#
  } else { "" };
  format!(
    r#"<tr class="{row_class}">
      <td class="text-nowrap">{start}{limit}</td>
      <td><span class="{inv}" title="{inv_title}"></span></td>
      <td>{dur}</td>
      <td>{cost}</td>
      <td><details><summary>show</summary><pre class="log-snippet">{log}</pre></details></td>
    </tr>"#,
    row_class = row_class,
    start = esc(&start_disp),
    limit = limit_badge,
    inv = inv_class,
    inv_title = if run.invoked { "Invoked" } else { "Not invoked" },
    dur = esc(&run.duration),
    cost = esc(&run.cost),
    log = esc(&run.log),
  )
}

const SHOW_INITIAL: usize = 5;
// Hard cap on runs displayed per project (issue #7)
const SHOW_MAX: usize = 1280;

fn render_prep(prep: &PrepView) -> String {
  // Badge colour: green for INVOKE_CLAUDE, grey otherwise
  let badge_class = if prep.decision == "INVOKE_CLAUDE" { "bg-success" } else { "bg-secondary" };
  let reasons_html: String = if prep.reasons.is_empty() {
    String::new()
  } else {
    let items: String = prep.reasons.iter()
      .map(|r| format!("<li>{}</li>", esc(r)))
      .collect();
    format!(r#"<ul class="mb-0 small">{items}</ul>"#)
  };
  format!(
    r#"<p class="mb-1 small"><span class="badge {badge_class} me-1">prep: {decision}</span></p>{reasons}"#,
    badge_class = badge_class,
    decision = esc(&prep.decision),
    reasons = reasons_html,
  )
}

fn fmt_tokens(n: u64) -> String {
  if n == 0 { return "—".into(); }
  if n >= 1_000_000 { return format!("{:.1}M", n as f64 / 1_000_000.0); }
  if n >= 1_000     { return format!("{:.1}k", n as f64 / 1_000.0); }
  n.to_string()
}

fn fmt_money(usd: f64, rates: &ExchangeRates) -> String {
  if usd == 0.0 { return "—".into(); }
  let eur = usd * rates.usd_to_eur;
  let czk = usd * rates.usd_to_czk;
  format!("${usd:.3} / €{eur:.3} / {czk:.1} Kč")
}

fn render_token_stats(stats: &TokenStats, rates: &ExchangeRates) -> String {
  let buckets = [
    ("day",      &stats.day),
    ("week",     &stats.week),
    ("lifetime", &stats.life),
  ];
  let rows: String = buckets.iter().map(|(label, b)| {
    let tokens = b.tokens_in + b.tokens_out;
    format!(
      r#"<tr>
        <td class="text-muted small">{label}</td>
        <td class="text-end small">{tok}</td>
        <td class="small">{cost}</td>
      </tr>"#,
      label = label,
      tok   = fmt_tokens(tokens),
      cost  = esc(&fmt_money(b.cost_usd, rates)),
    )
  }).collect();
  format!(
    r#"<table class="table table-sm table-borderless mb-1 token-stats">
      <thead class="table-secondary">
        <tr>
          <th class="small py-0">period</th>
          <th class="small py-0 text-end">tokens</th>
          <th class="small py-0">cost (USD / EUR / CZK)</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"#,
    rows = rows,
  )
}

fn render_project(proj: &ProjectView, now_secs: i64, tz_offset_secs: i64, rates: &ExchangeRates) -> String {
  // Cap total runs at SHOW_MAX (issue #7)
  let all_runs = &proj.runs[..proj.runs.len().min(SHOW_MAX)];
  let total = all_runs.len();

  let rows: String = if total == 0 {
    r#"<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>"#.into()
  } else {
    all_runs.iter().enumerate()
      .map(|(i, r)| render_run_row(r, now_secs, tz_offset_secs, i >= SHOW_INITIAL))
      .collect()
  };

  // tbody carries data-initial so JS knows how many rows to keep when collapsing
  let tbody_id = format!("tbody-{}", esc(&proj.name));

  // Progressive reveal footer: show-more + collapse buttons (issue #7).
  // JS reads data-batch (current next-batch size) and updates it after each click.
  let tfoot_html = if total > SHOW_INITIAL {
    format!(
      r#"<tfoot class="runs-footer">
        <tr><td colspan="5">
          <button class="btn btn-link btn-sm p-0 show-more-btn" data-tbody="{tbid}" data-batch="5">5 more…</button>
          <button class="btn btn-link btn-sm p-0 ms-2 collapse-runs-btn d-none" data-tbody="{tbid}">collapse</button>
        </td></tr>
      </tfoot>"#,
      tbid = tbody_id,
    )
  } else {
    String::new()
  };

  let prep_html  = proj.prep.as_ref().map(render_prep).unwrap_or_default();
  let stats_html = proj.token_stats.as_ref()
    .map(|s| render_token_stats(s, rates))
    .unwrap_or_default();

  format!(
    r#"<div class="col-12 col-md-6 col-xl-4 col-xxl-3">
<section class="project-section h-100" id="proj-{id}">
  <h2 class="h5">{name}</h2>
  <p class="text-muted small mb-2">{path}</p>
  {prep_html}
  {stats_html}
  <div class="table-responsive">
    <table class="table table-sm table-bordered table-hover align-middle mb-0">
      <thead class="table-dark">
        <tr>
          <th>Start</th>
          <th title="Invoked"></th>
          <th>Duration</th>
          <th>Cost / Tokens</th>
          <th>Log</th>
        </tr>
      </thead>
      <tbody id="{tbody_id}" data-initial="{initial}">{rows}</tbody>
      {tfoot}
    </table>
  </div>
</section>
</div>"#,
    id         = esc(&proj.name),
    name       = esc(&proj.name),
    path       = esc(&proj.path),
    prep_html  = prep_html,
    stats_html = stats_html,
    tbody_id   = tbody_id,
    initial    = SHOW_INITIAL,
    rows       = rows,
    tfoot      = tfoot_html,
  )
}

// ---------------------------------------------------------------------------
// Public WASM API
// ---------------------------------------------------------------------------

/// Render dashboard HTML from raw CBOR bytes.
/// now_secs: current UTC epoch seconds (from JS Date.now()/1000). u32 avoids BigInt.
/// tz_offset_secs: browser UTC offset in seconds (positive = east of UTC).
///   Pass -(new Date().getTimezoneOffset()) * 60 from JS. i32 covers ±12 h = ±43200 s.
/// Returns an HTML string on success, or an error message prefixed with "ERROR:".
#[wasm_bindgen]
pub fn render_dashboard(cbor_bytes: &[u8], now_secs: u32, tz_offset_secs: i32) -> String {
  let now_secs = now_secs as i64;
  let tz_offset_secs = tz_offset_secs as i64;
  match decode_data(cbor_bytes) {
    Err(e) => format!("ERROR: {e}"),
    Ok((generated_at, rates, projects)) => {
      let sections: String = projects.iter().map(|p| render_project(p, now_secs, tz_offset_secs, &rates)).collect();
      let nav: String = projects.iter().map(|p|
        format!(r##"<li class="nav-item"><a class="nav-link" href="#proj-{id}">{name}</a></li>"##,
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
