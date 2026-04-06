#!/usr/bin/env python3
# generate-data.py: Read clanker.log files from configured projects,
# emit www/data.cbor and regenerate the static content in www/index.html.
# Requires: cbor2 (pip install cbor2)

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


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect(config):
    """Collect data from all configured project paths."""
    projects = []
    for entry in config.get("projects", []):
        path = Path(entry["path"]).expanduser()
        log_path = path / "clanker.log"
        name = entry.get("name") or path.name
        runs = parse_log(log_path) if log_path.exists() else []
        projects.append({
            "name": name,
            "path": str(path),
            "runs": runs,
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


def render_run_row(run, now):
    """Render one <tr> for a run."""
    # limit_hit takes priority over invoked for row colour
    if run.get("limit_hit"):
        tr_class = "table-danger"
    elif run["invoked"]:
        tr_class = "table-warning"
    else:
        tr_class = ""
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


def render_project_html(proj, now):
    """Render HTML for one project section wrapped in a Bootstrap column div."""
    name = html.escape(proj["name"])
    path = html.escape(proj["path"])
    proj_id = html.escape(proj["name"])
    runs = proj["runs"]

    visible = runs[:SHOW_INITIAL]
    extra   = runs[SHOW_INITIAL:]

    visible_rows = "".join(render_run_row(r, now) for r in visible) if visible else \
        '<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>'

    # Extra runs: second tbody hidden by default (no JS = stays hidden)
    extra_html = ""
    if extra:
        extra_rows = "".join(render_run_row(r, now) for r in extra)
        n    = len(extra)
        tbid = html.escape(f"extra-{proj['name']}")
        extra_html = f"""
      <tbody id="{tbid}" class="d-none">{extra_rows}
      </tbody>
      <tfoot><tr><td colspan="5">
        <button class="btn btn-link btn-sm p-0 show-more-btn" data-target="{tbid}">Show {n} more\u2026</button>
      </td></tr></tfoot>"""

    return f"""
  <div class="col-12 col-md-6 col-xl-4 col-xxl-3">
    <section class="project-section h-100" id="proj-{proj_id}">
      <h2 class="h5">{name}</h2>
      <p class="text-muted small mb-2">{path}</p>
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
          <tbody>{visible_rows}
          </tbody>{extra_html}
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
        print(f"  {proj['name']}: {len(proj['runs'])} run(s)")

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
