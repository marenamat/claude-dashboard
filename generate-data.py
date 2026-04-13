#!/usr/bin/env python3
# generate-data.py: Read clanker run data from configured projects,
# emit www/data.cbor and regenerate the static content in www/index.html.
# Reads clanker-runs.jsonl (structured, preferred) or falls back to
# clanker.log (legacy).  Also reads clanker-prep.json for prep metadata.
# Requires: cbor2 (pip install cbor2)

import json
import os
import re
import sys
import html
import cbor2
import yaml
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SELFDIR = Path(__file__).parent.resolve()
CONFIG_PATH = SELFDIR / "config.yaml"
WWW = SELFDIR / "www"
MAX_LOG_LINES = 40   # log lines kept per run
MAX_RUNS = 50        # most recent runs kept per project
SHOW_INITIAL = 5     # runs shown by default; rest behind "show more"
SHOW_MAX = 1280      # hard cap on runs displayed per project (issue #7)


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# Patterns for token/cost lines emitted by Claude Code CLI
RE_COST        = re.compile(r"Total cost:\s*\$?([\d.]+)", re.IGNORECASE)
RE_TOKENS_IN   = re.compile(r"Input tokens?:\s*([\d,]+)", re.IGNORECASE)
RE_TOKENS_OUT  = re.compile(r"Output tokens?:\s*([\d,]+)", re.IGNORECASE)
# "You've hit your limit · resets 11pm" or "resets at 11pm" etc.
RE_LIMIT_RESET = re.compile(r"you'?ve hit your limit.*?reset\w*\s+(?:at\s+)?([^\s.,\n]+)", re.IGNORECASE)


def parse_date_line(line):
    """Parse the date lines written by `date` command.  Returns datetime or None."""
    line = line.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%a %b %d %H:%M:%S %Z %Y",   # Sun Apr  5 19:16:37 CEST 2026
        "%a %b %d %H:%M:%S %Y",
    ):
        try:
            dt = datetime.strptime(line, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def parse_log(log_path):
    """Parse a clanker.log file into a list of run dicts."""
    try:
        text = log_path.read_text(errors="replace")
    except OSError as e:
        print(f"  Warning: cannot read {log_path}: {e}", file=sys.stderr)
        return []

    blocks = re.split(r"^===========\s*$", text, flags=re.MULTILINE)
    runs = []
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        run = {
            "start": None,
            "end": None,
            "invoked": False,
            "limit_hit": False,
            "limit_reset": None,   # e.g. "11pm" if reset time was detected
            "cost_usd": None,
            "tokens_in": None,
            "tokens_out": None,
            "log": "",
            "permission_denials": [],
        }

        # First non-empty line is the start date
        for i, line in enumerate(lines):
            if line.strip():
                run["start"] = parse_date_line(line)
                lines = lines[i + 1:]
                break

        # Last non-empty line *may* be the end date
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                dt = parse_date_line(lines[i])
                if dt is not None:
                    run["end"] = dt
                    lines = lines[:i]
                break

        # Scan body for invocation signal, rate-limit hit, and token data
        for line in lines:
            if "INVOKE_CLAUDE" in line or "Prep decision: INVOKE_CLAUDE" in line:
                run["invoked"] = True
            if "you've hit your limit" in line.lower():
                run["limit_hit"] = True
                m = RE_LIMIT_RESET.search(line)
                if m and run["limit_reset"] is None:
                    run["limit_reset"] = m.group(1)
            # Cross-check: if the run produced a successful result record
            # (stream-json format), clear spurious limit_hit set from echoed context.
            stripped = line.strip()
            if stripped.startswith("{") and '"type"' in stripped:
                try:
                    jl = json.loads(stripped)
                    t = jl.get("type")
                    if t == "result" and not jl.get("is_error", True):
                        run["limit_hit"] = False
                    elif t == "rate_limit_event" and run["limit_reset"] is None:
                        resets_at = jl.get("rate_limit_info", {}).get("resetsAt")
                        if resets_at:
                            try:
                                dt = datetime.fromtimestamp(int(resets_at), tz=timezone.utc)
                                run["limit_reset"] = dt.strftime("%H:%M UTC")
                            except (ValueError, OSError, OverflowError):
                                pass
                except (json.JSONDecodeError, ValueError):
                    pass
            m = RE_COST.search(line)
            if m:
                try:
                    run["cost_usd"] = float(m.group(1))
                except ValueError:
                    pass
            m = RE_TOKENS_IN.search(line)
            if m:
                try:
                    run["tokens_in"] = int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
            m = RE_TOKENS_OUT.search(line)
            if m:
                try:
                    run["tokens_out"] = int(m.group(1).replace(",", ""))
                except ValueError:
                    pass

        run["log"] = "\n".join(lines[-MAX_LOG_LINES:])

        if run["start"] is not None:
            runs.append(run)

    # Newest first; caller is responsible for capping (issue #11)
    runs.sort(key=lambda r: r["start"], reverse=True)
    return runs


def _parse_denial(d):
    """Normalise one permission-denial entry to {"tool": str, "input": str}."""
    if isinstance(d, str):
        return {"tool": d, "input": ""}
    if not isinstance(d, dict):
        return {"tool": str(d), "input": ""}
    # Try common field names for the tool name
    tool = d.get("name") or d.get("tool_name") or d.get("tool") or "unknown"
    inp  = d.get("input") or d.get("command") or ""
    if isinstance(inp, dict):
        # Summarise dict inputs as "key=value, ..." (first two keys, truncated)
        inp = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(inp.items())[:2])
    return {"tool": str(tool), "input": str(inp)[:120]}


def _parse_iso(s):
    """Parse an ISO 8601 string (with offset) into a timezone-aware datetime, or None."""
    if not s:
        return None
    try:
        # Python 3.11+ handles this natively; handle +00:00 suffix for older versions
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def parse_jsonl(jsonl_path):
    """Parse a clanker-runs.jsonl file into a list of run dicts.

    Each JSON line must have at minimum a "start" field (ISO 8601).
    Unknown fields are ignored so older parsers stay forward-compatible.
    """
    try:
        text = jsonl_path.read_text(errors="replace")
    except OSError as e:
        print(f"  Warning: cannot read {jsonl_path}: {e}", file=sys.stderr)
        return []

    runs = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  Warning: {jsonl_path}:{lineno}: JSON parse error: {e}", file=sys.stderr)
            continue

        start = _parse_iso(rec.get("start"))
        if start is None:
            print(f"  Warning: {jsonl_path}:{lineno}: missing/invalid 'start', skipping", file=sys.stderr)
            continue

        limit_hit   = bool(rec.get("limit_hit", False))
        limit_reset = rec.get("limit_reset")

        # Cross-check limit_hit against the actual result in the log_excerpt.
        # clanker may set limit_hit=True if the rate-limit string appears anywhere
        # in the log, including from a previous run's context.  If the last
        # {"type":"result"} record shows is_error=false, the run actually succeeded.
        # Also extract the reset time from the result message or rate_limit_event.
        log_excerpt = rec.get("log_excerpt", "")
        permission_denials = []  # list of {"tool": str, "input": str}
        for log_line in log_excerpt.splitlines():
            log_line = log_line.strip()
            if not log_line:
                continue
            try:
                jl = json.loads(log_line)
            except (json.JSONDecodeError, ValueError):
                continue
            t = jl.get("type")
            if t == "result":
                if not jl.get("is_error", True):
                    # Run completed successfully — clear spurious limit flag
                    limit_hit = False
                elif limit_reset is None:
                    # Extract reset time from result message e.g. "resets 12pm ..."
                    m = RE_LIMIT_RESET.search(jl.get("result", ""))
                    if m:
                        limit_reset = m.group(1)
                # Format A: {"type":"result","permission_denials":[...]}
                for d in (jl.get("permission_denials") or []):
                    permission_denials.append(_parse_denial(d))
            elif t == "rate_limit_event" and limit_reset is None:
                # rate_limit_event carries a Unix timestamp in resetsAt; convert
                # to a simple HH:MM string so the badge is informative.
                resets_at = jl.get("rate_limit_info", {}).get("resetsAt")
                if resets_at:
                    try:
                        dt = datetime.fromtimestamp(int(resets_at), tz=timezone.utc)
                        limit_reset = dt.strftime("%H:%M UTC")
                    except (ValueError, OSError, OverflowError):
                        pass
            # Format B: {"type":"system","subtype":"permission_denied","tool_use":{...}}
            if t == "system" and jl.get("subtype") == "permission_denied":
                permission_denials.append(_parse_denial(jl.get("tool_use") or jl))
            # Format C: any record with a top-level "permission_denials" field
            if "permission_denials" in jl and t != "result":
                for d in (jl["permission_denials"] or []):
                    permission_denials.append(_parse_denial(d))

        runs.append({
            "start":               start,
            "end":                 _parse_iso(rec.get("end")),
            "invoked":             bool(rec.get("invoked", False)),
            "limit_hit":           limit_hit,
            "limit_reset":         limit_reset,
            "cost_usd":            rec.get("cost_usd"),       # float or None
            "tokens_in":           rec.get("tokens_in"),      # int or None
            "tokens_out":          rec.get("tokens_out"),     # int or None
            "exit_code":           rec.get("exit_code"),      # int or None
            "log":                 log_excerpt,
            "permission_denials":  permission_denials,        # list of {"tool","input"}
        })

    # Newest first; caller is responsible for capping
    runs.sort(key=lambda r: r["start"], reverse=True)
    return runs


def parse_prep(prep_path):
    """Read clanker-prep.json and return a dict with prep metadata, or None."""
    if not prep_path.exists():
        return None
    try:
        with open(prep_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Warning: cannot read {prep_path}: {e}", file=sys.stderr)
        return None
    return {
        "decision": data.get("decision", ""),
        "reasons":  data.get("reasons", []),
    }


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def fetch_exchange_rates():
    """Fetch USD→EUR and USD→CZK rates from ECB.

    Returns {"usd_to_eur": float, "usd_to_czk": float}.
    Falls back to hardcoded approximations if the request fails.
    ECB gives rates relative to EUR (e.g. USD rate = how many USD per 1 EUR).
    """
    try:
        url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
        req = urllib.request.Request(url, headers={"Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        ns = {"ns": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
        ecb_rates = {}
        for cube in root.findall(".//ns:Cube[@currency]", ns):
            ecb_rates[cube.get("currency")] = float(cube.get("rate"))
        usd_per_eur = ecb_rates.get("USD", 1.09)
        czk_per_eur = ecb_rates.get("CZK", 25.2)
        rates = {
            "usd_to_eur": 1.0 / usd_per_eur,
            "usd_to_czk": czk_per_eur / usd_per_eur,
        }
        print(f"  Exchange rates: 1 USD = {rates['usd_to_eur']:.4f} EUR = {rates['usd_to_czk']:.3f} CZK")
        return rates
    except Exception as e:
        print(f"  Warning: ECB rate fetch failed ({e}); using hardcoded fallback", file=sys.stderr)
        return {"usd_to_eur": 0.92, "usd_to_czk": 23.0}


def compute_token_stats(runs, now):
    """Compute token/cost totals over last day, last week, and lifetime.

    Uses ALL runs (not the display-capped slice) for accurate lifetime figures.
    Returns {"day": {...}, "week": {...}, "life": {...}} where each bucket has
    tokens_in, tokens_out, cost_usd.
    """
    day_cutoff  = now - timedelta(hours=24)
    week_cutoff = now - timedelta(days=7)

    def agg(subset):
        t_in  = sum(r["tokens_in"]  or 0 for r in subset)
        t_out = sum(r["tokens_out"] or 0 for r in subset)
        cost  = sum(r["cost_usd"]   or 0.0 for r in subset)
        return {"tokens_in": t_in, "tokens_out": t_out, "cost_usd": cost}

    return {
        "day":  agg([r for r in runs if r["start"] and r["start"] >= day_cutoff]),
        "week": agg([r for r in runs if r["start"] and r["start"] >= week_cutoff]),
        "life": agg(runs),
    }


def collect(config):
    """Collect data from all configured project paths."""
    now = datetime.now(timezone.utc)

    print("Fetching exchange rates...")
    exchange_rates = fetch_exchange_rates()

    projects = []
    for entry in config.get("projects", []):
        path = Path(entry["path"]).expanduser()
        name = entry.get("name") or path.name

        jsonl_path = path / "clanker-runs.jsonl"
        log_path   = path / "clanker.log"

        # Prefer structured JSONL; fall back to legacy log parsing
        if jsonl_path.exists():
            print(f"  {name}: reading structured {jsonl_path.name}")
            all_runs = parse_jsonl(jsonl_path)
        elif log_path.exists():
            print(f"  {name}: reading legacy {log_path.name}")
            all_runs = parse_log(log_path)
        else:
            all_runs = []

        # Compute stats from ALL runs before capping for display (issue #11)
        token_stats = compute_token_stats(all_runs, now)

        prep = parse_prep(path / "clanker-prep.json")

        projects.append({
            "name":         name,
            "path":         str(path),
            "runs":         all_runs[:MAX_RUNS],
            "prep":         prep,          # None or {"decision": ..., "reasons": [...]}
            "token_stats":  token_stats,   # day/week/life token+cost totals (issue #11)
        })
    return {
        "generated_at":   now,
        "exchange_rates": exchange_rates,
        "projects":       projects,
    }


# ---------------------------------------------------------------------------
# CBOR output
# ---------------------------------------------------------------------------

def write_cbor(data, out_path):
    """Serialise data to CBOR, writing to out_path."""
    def prepare(obj):
        if isinstance(obj, datetime):
            return obj
        if isinstance(obj, dict):
            return {k: prepare(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [prepare(v) for v in obj]
        return obj

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        cbor2.dump(prepare(data), f, timezone=timezone.utc)
    print(f"  Wrote {out_path} ({out_path.stat().st_size} bytes)")


# ---------------------------------------------------------------------------
# Static HTML generation
# ---------------------------------------------------------------------------

WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
MONTHS   = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_dt_relative(dt, now):
    """Format a datetime relative to now (UTC — static fallback only).

    The JS/WASM path uses the browser timezone (issue #8); this function only
    runs for the no-JS static fallback so UTC is acceptable.

    - same day        → "today HH:MM"
    - 1 day ago       → "yesterday HH:MM"
    - 2–5 days ago    → "weekday HH:MM"
    - older           → "Apr 01, HH:MM"
    """
    if dt is None:
        return "—"
    dt_utc  = dt.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    # Compare calendar days in UTC
    dt_day  = dt_utc.date()
    now_day = now_utc.date()
    days_ago = (now_day - dt_day).days
    hhmm = dt_utc.strftime("%H:%M")
    if days_ago == 0:
        return f"today {hhmm}"
    elif days_ago == 1:
        return f"yesterday {hhmm}"
    elif 2 <= days_ago <= 5:
        # weekday name of the run date (Monday=0 in Python isoweekday; we want Sun=0)
        wd = dt_day.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
        return f"{WEEKDAYS[wd]} {hhmm}"
    else:
        return f"{MONTHS[dt_utc.month - 1]} {dt_utc.day:02d}, {hhmm}"


def fmt_duration(run):
    """Format run duration, or return '—'."""
    if run["start"] and run["end"]:
        secs = int((run["end"] - run["start"]).total_seconds())
        if secs < 0:
            return "—"
        m, s = divmod(secs, 60)
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
    return "—"


def fmt_cost(run):
    """Format cost string."""
    if run["cost_usd"] is not None:
        return f"${run['cost_usd']:.4f}"
    if run["tokens_in"] or run["tokens_out"]:
        parts = []
        if run["tokens_in"]:
            parts.append(f"{run['tokens_in']:,} in")
        if run["tokens_out"]:
            parts.append(f"{run['tokens_out']:,} out")
        return " / ".join(parts)
    return "—"


def render_run_row(run, now, hidden=False):
    """Render one <tr> for a run.

    hidden=True marks the row with run-hidden d-none so JS can progressively
    reveal it (issue #7).
    """
    # limit_hit takes priority over invoked for row colour
    classes = []
    if run.get("limit_hit"):
        classes.append("table-danger")
    elif run["invoked"]:
        classes.append("table-warning")
    if hidden:
        classes.extend(["run-hidden", "d-none"])
    tr_class = " ".join(classes)
    inv_class = "inv-dot inv-yes" if run["invoked"] else "inv-dot inv-no"
    inv_title = "Invoked" if run["invoked"] else "Not invoked"
    if run.get("limit_hit"):
        reset_str = run.get("limit_reset")
        limit_title = f"Hit rate limit; resets {html.escape(reset_str)}" if reset_str else "Hit rate limit"
        reset_label = f" · resets {html.escape(reset_str)}" if reset_str else ""
        limit_badge = f'<span class="badge bg-danger ms-1" title="{limit_title}">limit{reset_label}</span>'
    else:
        limit_badge = ""
    denied = run.get("permission_denials") or []
    if denied:
        denials_json = html.escape(json.dumps(denied), quote=True)
        denied_badge = (
            f'<button type="button" class="badge bg-warning text-dark border-0 ms-1 denied-btn"'
            f' data-denials="{denials_json}" title="Denied permissions: {len(denied)} occurrence(s)">'
            f'{len(denied)} denied</button>'
        )
    else:
        denied_badge = ""
    start_str = html.escape(fmt_dt_relative(run["start"], now))
    log_escaped = html.escape(run["log"])
    return f"""
      <tr class="{tr_class}">
        <td class="text-nowrap">{start_str}{limit_badge}{denied_badge}</td>
        <td><span class="{inv_class}" title="{inv_title}"></span></td>
        <td>{html.escape(fmt_duration(run))}</td>
        <td>{html.escape(fmt_cost(run))}</td>
        <td>
          <details>
            <summary>show</summary>
            <pre class="log-snippet">{log_escaped}</pre>
          </details>
        </td>
      </tr>"""


def render_prep_html(prep):
    """Render a small prep-decision snippet, or return empty string."""
    if not prep:
        return ""
    decision = html.escape(prep.get("decision", ""))
    reasons  = prep.get("reasons", [])
    if not decision:
        return ""
    badge_class = "bg-success" if decision == "INVOKE_CLAUDE" else "bg-secondary"
    reasons_html = ""
    if reasons:
        items = "".join(f"<li>{html.escape(r)}</li>" for r in reasons)
        reasons_html = f'<ul class="mb-0 small">{items}</ul>'
    return (
        f'<p class="mb-1 small">'
        f'<span class="badge {badge_class} me-1">prep: {decision}</span>'
        f'</p>{reasons_html}'
    )


def render_token_stats_html(stats, rates):
    """Render a compact token-statistics table (issue #11).

    stats: {"day": {...}, "week": {...}, "life": {...}}
    rates: {"usd_to_eur": float, "usd_to_czk": float}
    """
    if not stats:
        return ""
    usd_to_eur = rates.get("usd_to_eur", 0.92)
    usd_to_czk = rates.get("usd_to_czk", 23.0)

    def fmt_tokens(n):
        if n == 0:
            return "—"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}k"
        return str(n)

    def fmt_money(usd):
        if usd == 0.0:
            return "—"
        eur = usd * usd_to_eur
        czk = usd * usd_to_czk
        return (f'<span title="${usd:.4f} / €{eur:.4f} / {czk:.2f} Kč">'
                f'${usd:.3f} / €{eur:.3f} / {czk:.1f} Kč</span>')

    rows = ""
    for label, key in (("day", "day"), ("week", "week"), ("lifetime", "life")):
        b = stats.get(key, {})
        t_in  = b.get("tokens_in",  0) or 0
        t_out = b.get("tokens_out", 0) or 0
        cost  = b.get("cost_usd",  0.0) or 0.0
        rows += (
            f'<tr><td class="text-muted small">{label}</td>'
            f'<td class="text-end small">{fmt_tokens(t_in + t_out)}</td>'
            f'<td class="small">{fmt_money(cost)}</td></tr>'
        )

    return f"""
      <table class="table table-sm table-borderless mb-1 token-stats">
        <thead class="table-secondary"><tr>
          <th class="small py-0">period</th>
          <th class="small py-0 text-end">tokens</th>
          <th class="small py-0">cost (USD / EUR / CZK)</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>"""


def render_project_html(proj, now, rates=None):
    """Render HTML for one project section wrapped in a Bootstrap column div."""
    name = html.escape(proj["name"])
    path = html.escape(proj["path"])
    proj_id = html.escape(proj["name"])
    # Cap at SHOW_MAX (issue #7)
    runs = proj["runs"][:SHOW_MAX]
    total = len(runs)

    if total == 0:
        all_rows = '<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>'
    else:
        all_rows = "".join(
            render_run_row(r, now, hidden=(i >= SHOW_INITIAL))
            for i, r in enumerate(runs)
        )

    tbody_id = html.escape(f"tbody-{proj['name']}")

    # Progressive reveal tfoot: show-more + collapse (issue #7).
    # Without JS the hidden rows stay hidden — same fallback behaviour as before.
    tfoot_html = ""
    if total > SHOW_INITIAL:
        tfoot_html = f"""
      <tfoot class="runs-footer">
        <tr><td colspan="5">
          <button class="btn btn-link btn-sm p-0 show-more-btn" data-tbody="{tbody_id}" data-batch="5">5 more\u2026</button>
          <button class="btn btn-link btn-sm p-0 ms-2 collapse-runs-btn d-none" data-tbody="{tbody_id}">collapse</button>
        </td></tr>
      </tfoot>"""

    prep_html  = render_prep_html(proj.get("prep"))
    stats_html = render_token_stats_html(proj.get("token_stats"), rates or {})

    return f"""
  <div class="col-12 col-md-6 col-xl-4 col-xxl-3">
    <section class="project-section h-100" id="proj-{proj_id}">
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
          <tbody id="{tbody_id}" data-initial="{SHOW_INITIAL}">{all_rows}
          </tbody>{tfoot_html}
        </table>
      </div>
    </section>
  </div>"""


def write_html(data, template_path, out_path):
    """Inject generated project content into the HTML template."""
    template = template_path.read_text()
    now = data["generated_at"]
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Nav links
    nav_items = "".join(
        f'<li class="nav-item"><a class="nav-link" href="#proj-{html.escape(p["name"])}">'
        f'{html.escape(p["name"])}</a></li>'
        for p in data["projects"]
    )

    # Project sections (each is a col-* div)
    rates = data.get("exchange_rates", {})
    sections = "".join(render_project_html(p, now, rates) for p in data["projects"])
    if not sections:
        sections = '<div class="col-12"><p class="text-muted">No projects configured. Edit <code>config.yaml</code>.</p></div>'

    content = (template
        .replace("{{GENERATED_AT}}", generated_at)
        .replace("{{NAV_ITEMS}}", nav_items)
        .replace("{{PROJECT_SECTIONS}}", sections))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    print(f"  Wrote {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not CONFIG_PATH.exists():
        print(f"No config found at {CONFIG_PATH}. Creating default.", file=sys.stderr)
        default_config = {"projects": [{"path": str(SELFDIR), "name": "claude-dashboard"}]}
        CONFIG_PATH.write_text(yaml.dump(default_config, default_flow_style=False))

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    print("Collecting data...")
    data = collect(config)
    for proj in data["projects"]:
        prep_str = f", prep={proj['prep']['decision']}" if proj.get("prep") else ""
        print(f"  {proj['name']}: {len(proj['runs'])} run(s){prep_str}")

    print("Writing CBOR...")
    write_cbor(data, WWW / "data.cbor")

    print("Writing HTML...")
    template_path = WWW / "index.template.html"
    if not template_path.exists():
        print(f"  Template {template_path} missing, skipping HTML generation.")
    else:
        write_html(data, template_path, WWW / "index.html")

    print("Done.")


if __name__ == "__main__":
    main()
