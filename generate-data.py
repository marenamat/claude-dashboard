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
RE_COST       = re.compile(r"Total cost:\s*\$?([\d.]+)", re.IGNORECASE)
RE_TOKENS_IN  = re.compile(r"Input tokens?:\s*([\d,]+)", re.IGNORECASE)
RE_TOKENS_OUT = re.compile(r"Output tokens?:\s*([\d,]+)", re.IGNORECASE)


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
            "cost_usd": None,
            "tokens_in": None,
            "tokens_out": None,
            "log": "",
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

    runs.sort(key=lambda r: r["start"], reverse=True)
    return runs[:MAX_RUNS]


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

        runs.append({
            "start":      start,
            "end":        _parse_iso(rec.get("end")),
            "invoked":    bool(rec.get("invoked", False)),
            "limit_hit":  bool(rec.get("limit_hit", False)),
            "cost_usd":   rec.get("cost_usd"),       # float or None
            "tokens_in":  rec.get("tokens_in"),      # int or None
            "tokens_out": rec.get("tokens_out"),     # int or None
            "exit_code":  rec.get("exit_code"),      # int or None
            "log":        rec.get("log_excerpt", ""),
        })

    # Newest first, cap
    runs.sort(key=lambda r: r["start"], reverse=True)
    return runs[:MAX_RUNS]


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

def collect(config):
    """Collect data from all configured project paths."""
    projects = []
    for entry in config.get("projects", []):
        path = Path(entry["path"]).expanduser()
        name = entry.get("name") or path.name

        jsonl_path = path / "clanker-runs.jsonl"
        log_path   = path / "clanker.log"

        # Prefer structured JSONL; fall back to legacy log parsing
        if jsonl_path.exists():
            print(f"  {name}: reading structured {jsonl_path.name}")
            runs = parse_jsonl(jsonl_path)
        elif log_path.exists():
            print(f"  {name}: reading legacy {log_path.name}")
            runs = parse_log(log_path)
        else:
            runs = []

        prep = parse_prep(path / "clanker-prep.json")

        projects.append({
            "name": name,
            "path": str(path),
            "runs": runs,
            "prep": prep,   # None or {"decision": ..., "reasons": [...]}
        })
    return {
        "generated_at": datetime.now(timezone.utc),
        "projects": projects,
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
    """Format a datetime relative to now.

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
    limit_badge = ('<span class="badge bg-danger ms-1" title="Hit rate limit">limit</span>'
                   if run.get("limit_hit") else "")
    start_str = html.escape(fmt_dt_relative(run["start"], now))
    log_escaped = html.escape(run["log"])
    return f"""
      <tr class="{tr_class}">
        <td class="text-nowrap">{start_str}{limit_badge}</td>
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


def render_project_html(proj, now):
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

    prep_html = render_prep_html(proj.get("prep"))

    return f"""
  <div class="col-12 col-md-6 col-xl-4 col-xxl-3">
    <section class="project-section h-100" id="proj-{proj_id}">
      <h2 class="h5">{name}</h2>
      <p class="text-muted small mb-2">{path}</p>
      {prep_html}
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
    sections = "".join(render_project_html(p, now) for p in data["projects"])
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
