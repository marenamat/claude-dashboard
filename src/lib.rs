// lib.rs: Claude Dashboard WebAssembly module.
// Receives CBOR data bytes from JS, renders the dashboard HTML.

use wasm_bindgen::prelude::*;
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// CBOR deserialisation helpers
// ---------------------------------------------------------------------------

fn decode_data(bytes: &[u8]) -> Result<(String, ExchangeRates, Vec<ProjectView>, Vec<SpawnEvent>), String> {
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

  // Spawner events (issue #15): optional list of {timestamp,action,issue,project,message}
  let spawner_events = match top.get("spawner_events") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_spawn_event).collect(),
    _ => vec![],
  };

  Ok((generated_at, rates, projects, spawner_events))
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
// - 2–3 days        → "weekday HH:MM"   (issue #19: only 3 days of relative names)
// - older           → "Mon 01 Apr HH:MM" (issue #19: always include DOW with explicit date)
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

  const WEEKDAYS:     [&str; 7]  = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
  const WEEKDAYS_ABB: [&str; 7]  = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS:       [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  match days_ago {
    0      => format!("today {hhmm}"),
    1      => format!("yesterday {hhmm}"),
    2..=3  => {
      // Weekday name for the past 2–3 days
      let wd = epoch_to_weekday(local_ts);
      format!("{} {hhmm}", WEEKDAYS[wd])
    }
    _ => {
      // Older: show abbreviated DOW + day + month so the day of week is always visible
      let wd = epoch_to_weekday(local_ts);
      let (_, m, d) = epoch_to_ymd(local_ts);
      format!("{} {:02} {} {hhmm}", WEEKDAYS_ABB[wd], d, MONTHS[(m - 1) as usize])
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
  start_raw:           String,  // raw ISO string for relative formatting
  invoked:             bool,
  limit_hit:           bool,
  limit_reset:         String,  // e.g. "11pm", empty if unknown
  duration:            String,
  cost_usd:            Option<f64>,   // raw USD cost; formatted at render time (issue #11)
  tokens_in:           Option<u64>,
  tokens_out:          Option<u64>,
  log:                 String,
  permission_denials:  Vec<(String, String)>,  // (tool_name, input_summary)
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

// One event from spawner-log.yaml (issue #15)
struct SpawnEvent {
  timestamp: String,
  action:    String,  // "spawned" | "error" | "skipped"
  issue:     Option<u64>,
  project:   String,
  message:   String,
}

struct ProjectView {
  name:           String,
  path:           String,
  runs:           Vec<RunView>,
  prep:           Option<PrepView>,
  token_stats:    Option<TokenStats>,
  clone_commands: String,  // bash setup snippet (issue #16), empty if unavailable
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

fn parse_spawn_event(v: &ciborium::value::Value) -> SpawnEvent {
  // Spawner events are maps with optional fields (issue #15)
  let map = val_as_map(v);
  let timestamp = val_as_str(&map, "timestamp");
  let action    = val_as_str(&map, "action");
  let project   = val_as_str(&map, "project");
  let message   = val_as_str(&map, "message");
  let issue     = val_as_u64(&map, "issue");
  SpawnEvent { timestamp, action, issue, project, message }
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
  // clone_commands is a pre-formatted bash snippet (issue #16)
  let clone_commands = val_as_str(&map, "clone_commands");
  ProjectView { name, path, runs, prep, token_stats, clone_commands }
}

fn parse_denial(v: &ciborium::value::Value) -> (String, String) {
  let m = val_as_map(v);
  let tool  = val_as_str(&m, "tool");
  let input = val_as_str(&m, "input");
  (tool, input)
}

fn parse_run(v: &ciborium::value::Value) -> RunView {
  let map = val_as_map(v);
  let start_raw = extract_text_or_tag(&map, "start").unwrap_or_default();
  let end_raw   = extract_text_or_tag(&map, "end").unwrap_or_default();
  let invoked     = val_as_bool(&map, "invoked");
  let limit_hit   = val_as_bool(&map, "limit_hit");
  let limit_reset = val_as_str(&map, "limit_reset");
  let cost_usd    = val_as_f64(&map, "cost_usd");
  let tokens_in  = val_as_u64(&map, "tokens_in");
  let tokens_out = val_as_u64(&map, "tokens_out");
  let log = val_as_str(&map, "log");

  let permission_denials = match map.get("permission_denials") {
    Some(ciborium::value::Value::Array(arr)) => arr.iter().map(parse_denial).collect(),
    _ => vec![],
  };

  let duration = if !start_raw.is_empty() && !end_raw.is_empty() {
    compute_duration(&start_raw, &end_raw)
  } else {
    "—".into()
  };

  RunView { start_raw, invoked, limit_hit, limit_reset, duration, cost_usd, tokens_in, tokens_out, log, permission_denials }
}

// ---------------------------------------------------------------------------
// Run grouping: collapse consecutive no-work / limit-hit entries (issue #17)
// ---------------------------------------------------------------------------

// Classify a run for collapsing purposes.
#[derive(PartialEq)]
enum RunKind { Normal, NoWork, LimitHit }

fn run_kind(run: &RunView) -> RunKind {
  if run.limit_hit      { RunKind::LimitHit }
  else if !run.invoked  { RunKind::NoWork }
  else                  { RunKind::Normal }
}

// A display item is either a single run or a collapsed group.
enum DisplayItem<'a> {
  Single(&'a RunView),
  // start_raw = oldest run in group, end_raw = newest run in group (display order is newest-first)
  Collapsed { kind: RunKind, start_raw: &'a str, end_raw: &'a str, count: usize },
}

// Group runs into display items.  Runs arrive newest-first.
// Groups of 2+ consecutive same-kind collapsible runs → Collapsed.
fn group_runs(runs: &[RunView]) -> Vec<DisplayItem<'_>> {
  let mut items: Vec<DisplayItem<'_>> = Vec::new();
  let mut i = 0;
  while i < runs.len() {
    let kind = run_kind(&runs[i]);
    if kind == RunKind::Normal {
      items.push(DisplayItem::Single(&runs[i]));
      i += 1;
      continue;
    }
    // Find how many consecutive runs share this kind
    let mut j = i + 1;
    while j < runs.len() && run_kind(&runs[j]) == kind {
      j += 1;
    }
    let count = j - i;
    if count >= 2 {
      // runs[i] is newest, runs[j-1] is oldest → "between oldest and newest"
      items.push(DisplayItem::Collapsed {
        kind,
        start_raw: &runs[j - 1].start_raw,  // oldest
        end_raw:   &runs[i].start_raw,       // newest
        count,
      });
    } else {
      items.push(DisplayItem::Single(&runs[i]));
    }
    i = j;
  }
  items
}

// Render one collapsed summary row.
fn render_collapsed_row(kind: &RunKind, start_raw: &str, end_raw: &str, count: usize,
  now_secs: i64, tz_offset_secs: i64, hidden: bool) -> String {

  let start_disp = fmt_ts_relative(start_raw, now_secs, tz_offset_secs);
  let end_disp   = fmt_ts_relative(end_raw,   now_secs, tz_offset_secs);

  let (row_class_base, label) = match kind {
    RunKind::NoWork   => ("text-muted",  "nothing to do"),
    RunKind::LimitHit => ("table-danger","limit hit"),
    RunKind::Normal   => unreachable!(),
  };
  let hidden_class = if hidden { " run-hidden d-none" } else { "" };
  let row_class = format!("{row_class_base}{hidden_class}");

  format!(
    r#"<tr class="{row_class}">
      <td colspan="5" class="text-center small fst-italic">
        between {start} and {end} — {label} ({count} runs)
      </td>
    </tr>"#,
    row_class = row_class,
    start = esc(&start_disp),
    end   = esc(&end_disp),
    label = label,
    count = count,
  )
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

// Escape a string for embedding inside a JSON string value (not the attribute).
fn esc_json(s: &str) -> String {
  s.replace('\\', r"\\")
   .replace('"',  r#"\""#)
   .replace('\n', r"\n")
   .replace('\r', r"\r")
   .replace('\t', r"\t")
}

fn render_run_row(run: &RunView, now_secs: i64, tz_offset_secs: i64, rates: &ExchangeRates, hidden: bool) -> String {
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
    if run.limit_reset.is_empty() {
      r#" <span class="badge bg-danger ms-1" title="Hit rate limit">limit</span>"#.to_owned()
    } else {
      format!(
        r#" <span class="badge bg-danger ms-1" title="Hit rate limit; resets {reset}">limit · resets {reset}</span>"#,
        reset = esc(&run.limit_reset),
      )
    }
  } else { String::new() };
  // Log cell: a "show" button that opens the log overlay (issue #10).
  // data-log holds the raw log text; JS reads it and populates the overlay.
  let log_btn = if run.log.is_empty() {
    r#"<span class="text-muted small">—</span>"#.to_owned()
  } else {
    format!(
      r#"<button type="button" class="btn btn-link btn-sm p-0 log-show-btn" data-log="{log}">show</button>"#,
      log = esc(&run.log),
    )
  };

  // Denied permissions badge (issue #12)
  let denied_badge = if !run.permission_denials.is_empty() {
    let n = run.permission_denials.len();
    // Build JSON array of {tool, input} for the JS overlay
    let entries: Vec<String> = run.permission_denials.iter().map(|(tool, inp)| {
      format!(r#"{{"tool":"{}","input":"{}"}}"#, esc_json(tool), esc_json(inp))
    }).collect();
    let json = format!("[{}]", entries.join(","));
    format!(
      r#" <button type="button" class="badge bg-warning text-dark border-0 ms-1 denied-btn" data-denials="{data}" title="Denied permissions: {n} occurrence(s)">{n} denied</button>"#,
      data = esc(&json),
      n = n,
    )
  } else { String::new() };

  // Format cost: USD/EUR/CZK if available, else token counts, else dash (issue #11)
  let cost_str = if let Some(c) = run.cost_usd.filter(|&v| v > 0.0) {
    fmt_money(c, rates)
  } else if run.tokens_in.is_some() || run.tokens_out.is_some() {
    let mut parts = vec![];
    if let Some(i) = run.tokens_in  { parts.push(format!("{i} in")); }
    if let Some(o) = run.tokens_out { parts.push(format!("{o} out")); }
    parts.join(" / ")
  } else {
    "—".into()
  };

  format!(
    r#"<tr class="{row_class}">
      <td class="text-nowrap">{start}{limit}{denied}</td>
      <td><span class="{inv}" title="{inv_title}"></span></td>
      <td>{dur}</td>
      <td class="text-nowrap small">{cost}</td>
      <td>{log_btn}</td>
    </tr>"#,
    row_class = row_class,
    start = esc(&start_disp),
    limit = limit_badge,
    denied = denied_badge,
    inv = inv_class,
    inv_title = if run.invoked { "Invoked" } else { "Not invoked" },
    dur = esc(&run.duration),
    cost = esc(&cost_str),
    log_btn = log_btn,
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

  // Group consecutive no-work / limit-hit runs into summary rows (issue #17)
  let display_items = group_runs(all_runs);
  let total = display_items.len();

  let rows: String = if total == 0 {
    r#"<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>"#.into()
  } else {
    display_items.iter().enumerate().map(|(i, item)| {
      let hidden = i >= SHOW_INITIAL;
      match item {
        DisplayItem::Single(r) =>
          render_run_row(r, now_secs, tz_offset_secs, rates, hidden),
        DisplayItem::Collapsed { kind, start_raw, end_raw, count } =>
          render_collapsed_row(kind, start_raw, end_raw, *count, now_secs, tz_offset_secs, hidden),
      }
    }).collect()
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

  // Clone button: opens an overlay with bash setup commands (issue #16)
  let clone_btn = if !proj.clone_commands.is_empty() {
    format!(
      r#" <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-1 clone-btn" data-clone-cmds="{cmds}" title="Show clone commands">clone</button>"#,
      cmds = esc(&proj.clone_commands),
    )
  } else {
    String::new()
  };

  format!(
    r#"<div class="col-12 col-md-6 col-xxl-4">
<section class="project-section h-100" id="proj-{id}">
  <h2 class="h5">{name}{clone_btn}</h2>
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
    clone_btn  = clone_btn,
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
// Spawner events rendering (issue #15)
// ---------------------------------------------------------------------------

fn render_spawner_events(events: &[SpawnEvent]) -> String {
  if events.is_empty() { return String::new(); }
  // Show most recent 20, newest first.
  let shown: Vec<&SpawnEvent> = events.iter().rev().take(20).collect();
  let rows: String = shown.iter().map(|e| {
    // Truncate ISO timestamp to "YYYY-MM-DD HH:MM"
    let ts = if e.timestamp.len() >= 16 {
      e.timestamp[..16].replace('T', " ")
    } else {
      e.timestamp.clone()
    };
    let badge_cls = match e.action.as_str() {
      "spawned" => "bg-success",
      "error"   => "bg-danger",
      _         => "bg-secondary",
    };
    let issue_link = match e.issue {
      Some(n) => format!(
        r##" <a href="https://github.com/marenamat/claude-dashboard/issues/{n}" class="text-muted small">#{n}</a>"##
      ),
      None => String::new(),
    };
    format!(
      r#"<tr>
        <td class="text-nowrap small text-muted">{ts}</td>
        <td><span class="badge {badge_cls}">{act}</span>{iss}</td>
        <td class="small">{prj}</td>
        <td class="small">{msg}</td>
      </tr>"#,
      ts      = esc(&ts),
      badge_cls = badge_cls,
      act     = esc(&e.action),
      iss     = issue_link,
      prj     = esc(&e.project),
      msg     = esc(&e.message),
    )
  }).collect();
  format!(
    r#"<div class="col-12">
  <section id="spawner-log" class="card mb-3">
    <div class="card-header"><strong>Spawner log</strong></div>
    <div class="card-body p-0">
      <table class="table table-sm mb-0">
        <thead class="table-secondary">
          <tr>
            <th class="small py-0">time</th>
            <th class="small py-0">action</th>
            <th class="small py-0">project</th>
            <th class="small py-0">message</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </section>
</div>"#,
    rows = rows,
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
    Ok((generated_at, rates, projects, spawner_events)) => {
      let sections: String = projects.iter().map(|p| render_project(p, now_secs, tz_offset_secs, &rates)).collect();
      let spawner_html = render_spawner_events(&spawner_events);
      let nav: String = projects.iter().map(|p|
        format!(r##"<li class="nav-item"><a class="nav-link" href="#proj-{id}">{name}</a></li>"##,
          id = esc(&p.name), name = esc(&p.name))
      ).collect();
      format!(
        r#"<div id="dash-nav-items">{nav}</div>
<div id="dash-generated-at">{gen}</div>
<div id="dash-content">{sections}{spawner}</div>"#,
        nav = nav,
        gen = esc(&generated_at),
        sections = sections,
        spawner = spawner_html,
      )
    }
  }
}

/// Return the version string.
#[wasm_bindgen]
pub fn version() -> String {
  env!("CARGO_PKG_VERSION").into()
}
