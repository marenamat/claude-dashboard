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
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SELFDIR = Path(__file__).parent.resolve()
CONFIG_PATH = SELFDIR / "config.yaml"
WWW = SELFDIR / "www"
MAX_LOG_LINES = 40   # log lines kept per run
MAX_RUNS = 50        # most recent runs kept per project


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# Patterns for token/cost lines emitted by Claude Code CLI
RE_COST    = re.compile(r"Total cost:\s*\$?([\d.]+)", re.IGNORECASE)
RE_TOKENS_IN  = re.compile(r"Input tokens?:\s*([\d,]+)", re.IGNORECASE)
RE_TOKENS_OUT = re.compile(r"Output tokens?:\s*([\d,]+)", re.IGNORECASE)


def parse_date_line(line):
    """Parse the date lines written by `date` command.  Returns datetime or None."""
    line = line.strip()
    # Try ISO format first (future-proof)
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

    # Split into raw run blocks on "==========="
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

        # Keep the last MAX_LOG_LINES lines as the log snippet
        run["log"] = "\n".join(lines[-MAX_LOG_LINES:])

        if run["start"] is not None:
            runs.append(run)

    # Sort by start time, newest first, cap
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
    # Convert datetime objects to timestamps for CBOR
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

def fmt_dt(dt):
    """Format datetime for display, or return '—'."""
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


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


def render_project_html(proj):
    """Render HTML for one project section."""
    name = html.escape(proj["name"])
    path = html.escape(proj["path"])
    runs = proj["runs"]

    rows = []
    for run in runs:
        # limit_hit takes priority over invoked for row colour
        if run.get("limit_hit"):
            row_class = "table-danger"
        elif run["invoked"]:
            row_class = "table-warning"
        else:
            row_class = ""
        limit_badge = ('<span class="badge bg-danger ms-1" title="Hit rate limit">limit</span>'
                       if run.get("limit_hit") else "")
        log_id = f"log-{id(run)}"
        log_escaped = html.escape(run["log"])
        rows.append(f"""
      <tr class="{row_class}">
        <td>{html.escape(fmt_dt(run["start"]))}{limit_badge}</td>
        <td>{"Yes" if run["invoked"] else "No"}</td>
        <td>{html.escape(fmt_duration(run))}</td>
        <td>{html.escape(fmt_cost(run))}</td>
        <td>
          <details>
            <summary>show</summary>
            <pre class="log-snippet" id="{log_id}">{log_escaped}</pre>
          </details>
        </td>
      </tr>""")

    rows_html = "".join(rows) if rows else \
        '<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>'

    return f"""
  <section class="project-section mb-5" id="proj-{html.escape(proj['name'])}">
    <h2 class="h4">{name}</h2>
    <p class="text-muted small">{path}</p>
    <div class="table-responsive">
      <table class="table table-sm table-bordered table-hover align-middle">
        <thead class="table-dark">
          <tr>
            <th>Start (UTC)</th>
            <th>Invoked</th>
            <th>Duration</th>
            <th>Cost / Tokens</th>
            <th>Log</th>
          </tr>
        </thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>
  </section>"""


def write_html(data, template_path, out_path):
    """Inject generated project content into the HTML template."""
    template = template_path.read_text()
    generated_at = fmt_dt(data["generated_at"])

    # Nav links
    nav_items = "".join(
        f'<li class="nav-item"><a class="nav-link" href="#proj-{html.escape(p["name"])}">'
        f'{html.escape(p["name"])}</a></li>'
        for p in data["projects"]
    )

    # Project sections
    sections = "".join(render_project_html(p) for p in data["projects"])
    if not sections:
        sections = '<p class="text-muted">No projects configured. Edit <code>config.yaml</code>.</p>'

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
