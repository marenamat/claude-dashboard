#!/usr/bin/env python3
# generate-data.py: Read clanker run data from configured projects,
# emit www/data.cbor and regenerate the static content in www/index.html.
# Reads clanker-runs.jsonl (structured, preferred) or falls back to
# clanker.log (legacy).  Also reads clanker-prep.json for prep metadata.
# Requires: cbor2 (pip install cbor2)

import json
import os
import re
import subprocess
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

SELFDIR      = Path(__file__).parent.resolve()
# Config search order: system-wide path first, then script directory.
_SYSTEM_CONFIG = Path("/etc/claude-dashboard/config.yaml")
CONFIG_PATH  = _SYSTEM_CONFIG if _SYSTEM_CONFIG.exists() else SELFDIR / "config.yaml"
SPAWNER_LOG  = SELFDIR / "spawner-log.yaml"   # written by spawner.py (issue #15)
WWW = SELFDIR / "www"
MAX_LOG_LINES = 40   # log lines kept per run
MAX_DAYS = 10        # days of history kept per project (issue #18)
SHOW_INITIAL = 5     # runs shown by default; rest behind "show more"
SHOW_MAX = 1280      # hard cap on runs displayed per project (issue #7)


# ---------------------------------------------------------------------------
# Git remote reading and clone command generation (issue #16)
# ---------------------------------------------------------------------------

def read_git_remotes(path):
    """Read git remotes from a project directory.

    Returns a list of {"name": str, "fetch": str, "push": str} dicts.
    Fetch and push URLs are the same when not explicitly set otherwise.
    Returns empty list if path is not a git repo or git is unavailable.
    """
    try:
        out = subprocess.check_output(
            ["git", "-C", str(path), "remote", "-v"],
            stderr=subprocess.DEVNULL, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    # "git remote -v" emits two lines per remote: one (fetch), one (push).
    remotes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[0], parts[1], parts[2].strip("()")
        if name not in remotes:
            remotes[name] = {"name": name, "fetch": url, "push": url}
        if kind == "push":
            remotes[name]["push"] = url
        elif kind == "fetch":
            remotes[name]["fetch"] = url
    return list(remotes.values())


def _github_ssh_url(https_url):
    """Convert https://github.com/org/repo to git@github.com:org/repo."""
    m = re.match(r"https://github\.com/(.+)", https_url)
    if m:
        return f"git@github.com:{m.group(1)}"
    return https_url


def make_clone_commands(path, remotes):
    """Generate bash setup commands for a human machine (issue #16).

    Determines:
      - GitHub remote (from 'github' or 'origin' remote, fetch URL)
      - claude-base remote (if present)
      - upstream remote (non-origin, non-github, non-claude-base, outside github.com/marenamat)
      - clanker remote (SSH path based on project path relative to ~/)

    Returns (commands_str, create_repo_url) where:
      - commands_str: bare git commands, no comment lines
      - create_repo_url: URL to create the GitHub repo (if applicable), else ""
    """
    home = Path.home()
    try:
        rel = path.resolve().relative_to(home)
        clanker_url = f"claude:{rel}"
    except ValueError:
        clanker_url = f"claude:{path}"

    # Find GitHub remote
    remote_by_name = {r["name"]: r for r in remotes}
    github_remote = remote_by_name.get("github") or remote_by_name.get("origin")
    github_fetch  = github_remote["fetch"] if github_remote else None
    github_push   = _github_ssh_url(github_fetch) if github_fetch else None

    # Derive clone dir from URL or path
    if github_fetch:
        clone_dir = github_fetch.rstrip("/").rsplit("/", 1)[-1]
        if clone_dir.endswith(".git"):
            clone_dir = clone_dir[:-4]
    else:
        clone_dir = path.name

    # claude-base remote
    claude_base_remote = remote_by_name.get("claude-base")

    # upstream: remotes that are not origin/github/claude-base/clanker and outside marenamat
    # (clanker is always added explicitly below, so skip it here to avoid duplicates)
    upstream_remotes = [
        r for r in remotes
        if r["name"] not in ("origin", "github", "claude-base", "clanker")
        and "github.com/marenamat" not in r["fetch"]
    ]

    lines = []
    create_repo_url = ""

    # Clone step
    if github_fetch:
        lines.append(f"git clone {github_fetch}")
        lines.append(f"cd {clone_dir}")
        lines.append(f"git remote rename origin github")
        if github_push and github_push != github_fetch:
            lines.append(f"git remote set-url --push github {github_push}")
        # Expose a link to create the repo on GitHub if it doesn't exist yet
        if "github.com" in github_fetch:
            create_repo_url = "https://github.com/new"
    else:
        lines.append(f"# clone manually and cd into {clone_dir}")

    lines.append(f"git remote add clanker {clanker_url}")

    if claude_base_remote:
        lines.append(f"git remote add claude-base {claude_base_remote['fetch']}")

    for r in upstream_remotes:
        lines.append(f"git remote add {r['name']} {r['fetch']}")

    return "\n".join(lines), create_repo_url


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
    """Normalise one permission-denial entry to {"tool": str, "input": str}.

    "input" is the human-readable offending action — the specific command,
    file path, or query that was blocked.  We extract the most meaningful
    field from the tool input dict rather than a generic key=value dump.
    """
    if isinstance(d, str):
        return {"tool": d, "input": ""}
    if not isinstance(d, dict):
        return {"tool": str(d), "input": ""}
    # Try common field names for the tool name
    tool = d.get("name") or d.get("tool_name") or d.get("tool") or "unknown"
    inp  = d.get("input") or d.get("command") or ""
    if isinstance(inp, dict):
        # Extract the most meaningful value for each known tool type
        if "command" in inp:
            action = str(inp["command"])       # Bash: the shell command
        elif "file_path" in inp:
            action = str(inp["file_path"])     # Read/Write/Edit/Glob/Grep
        elif "pattern" in inp:
            path = inp.get("path") or inp.get("file_path") or ""
            action = str(inp["pattern"])
            if path:
                action = f"{action}  (in {path})"
        elif "query" in inp:
            action = str(inp["query"])         # WebSearch/WebFetch
        elif "url" in inp:
            action = str(inp["url"])
        else:
            # Fallback: first key=value (first two keys, truncated)
            action = ", ".join(f"{k}={str(v)[:40]}" for k, v in list(inp.items())[:2])
        inp = action
    return {"tool": str(tool), "input": str(inp)[:300]}


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

        # Top-level cost/token fields (written by clanker-run; may be null/zero).
        cost_usd   = rec.get("cost_usd")    # float or None
        tokens_in  = rec.get("tokens_in")   # int or None
        tokens_out = rec.get("tokens_out")  # int or None

        # Parse log_excerpt JSONL lines to:
        #   - cross-check limit_hit / extract reset time (issue #5)
        #   - extract cost and token counts from result records (issue #11)
        #     (clanker-run historically left top-level cost/tokens as null/0;
        #      the actual totals live in total_cost_usd and usage on the result record)
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
                # Fallback: extract total_cost_usd from the result record (issue #11)
                if not cost_usd:
                    raw = jl.get("total_cost_usd")
                    if isinstance(raw, (int, float)) and raw:
                        cost_usd = float(raw)
                # Fallback: extract token counts from usage (issue #11)
                usage = jl.get("usage")
                if isinstance(usage, dict):
                    if tokens_in is None or tokens_in == 0:
                        # Sum all input variants: regular + cache creation + cache read
                        t_in = (
                            (usage.get("input_tokens") or 0)
                            + (usage.get("cache_creation_input_tokens") or 0)
                            + (usage.get("cache_read_input_tokens") or 0)
                        )
                        if t_in:
                            tokens_in = t_in
                    if tokens_out is None or tokens_out == 0:
                        t_out = usage.get("output_tokens") or 0
                        if t_out:
                            tokens_out = t_out
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
            "cost_usd":            cost_usd,           # float or None
            "tokens_in":           tokens_in,          # int or None
            "tokens_out":          tokens_out,         # int or None
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


def load_spawner_log():
    """Read spawner-log.yaml and return list of event dicts (issue #15).

    Each event has: timestamp, action, issue, project, message.
    Returns empty list if the file is absent or unreadable.
    """
    if not SPAWNER_LOG.exists():
        return []
    try:
        with open(SPAWNER_LOG) as f:
            data = yaml.safe_load(f) or {}
        events = data.get("events", [])
        # Normalise: ensure each entry has all expected keys.
        result = []
        for e in events:
            if not isinstance(e, dict):
                continue
            result.append({
                "timestamp": e.get("timestamp", ""),
                "action":    e.get("action", ""),
                "issue":     e.get("issue"),
                "project":   e.get("project", ""),
                "message":   e.get("message", ""),
            })
        return result
    except Exception as ex:
        print(f"  Warning: cannot read spawner-log.yaml: {ex}", file=sys.stderr)
        return []


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

        # Keep only the last MAX_DAYS days of history (issue #18)
        cutoff = now - timedelta(days=MAX_DAYS)
        recent_runs = [r for r in all_runs if r["start"] and r["start"] >= cutoff]

        prep = parse_prep(path / "clanker-prep.json")

        remotes = read_git_remotes(path)
        clone_commands, create_repo_url = make_clone_commands(path, remotes)

        projects.append({
            "name":            name,
            "path":            str(path),
            "runs":            recent_runs,
            "prep":            prep,             # None or {"decision": ..., "reasons": [...]}
            "token_stats":     token_stats,      # day/week/life token+cost totals (issue #11)
            "remotes":         remotes,           # list of {name, fetch, push} (issue #16)
            "clone_commands":  clone_commands,   # bare git commands (issue #16)
            "create_repo_url": create_repo_url,  # GitHub new-repo URL if applicable (issue #16)
        })
    print("Reading spawner log...")
    spawner_events = load_spawner_log()

    return {
        "generated_at":   now,
        "exchange_rates": exchange_rates,
        "projects":       projects,
        "spawner_events": spawner_events,  # list of spawn/error events (issue #15)
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

WEEKDAYS     = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
WEEKDAYS_ABB = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
MONTHS       = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_dt_relative(dt, now):
    """Format a datetime relative to now (UTC — static fallback only).

    The JS/WASM path uses the browser timezone (issue #8); this function only
    runs for the no-JS static fallback so UTC is acceptable.

    - same day        → "today HH:MM"
    - 1 day ago       → "yesterday HH:MM"
    - 2–3 days ago    → "weekday HH:MM"          (issue #19: only 3 days of relative names)
    - older           → "Mon 01 Apr HH:MM"        (issue #19: always show DOW with explicit date)
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
    # weekday index: Sun=0, Mon=1, ..., Sat=6
    wd = dt_day.isoweekday() % 7
    if days_ago == 0:
        return f"today {hhmm}"
    elif days_ago == 1:
        return f"yesterday {hhmm}"
    elif 2 <= days_ago <= 3:
        return f"{WEEKDAYS[wd]} {hhmm}"
    else:
        # Older: abbreviated DOW + day + month so the day of week is always visible
        return f"{WEEKDAYS_ABB[wd]} {dt_utc.day:02d} {MONTHS[dt_utc.month - 1]} {hhmm}"


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


def run_kind(run):
    """Classify a run for collapsing: 'limit_hit', 'no_work', or None (normal)."""
    if run.get("limit_hit"):
        return "limit_hit"
    if not run.get("invoked"):
        return "no_work"
    return None


def group_runs_for_display(runs):
    """Group consecutive no-work/limit-hit runs into summary entries (issue #17).

    Returns a list where each element is either:
      - a run dict (normal run, or lone single of a collapsible kind)
      - a dict {"_collapsed": True, "kind": ..., "start": datetime, "end": datetime, "count": int}

    Runs arrive newest-first; collapsed groups show oldest→newest range.
    Only groups of 2+ consecutive same-kind collapsible runs are collapsed.
    """
    result = []
    i = 0
    while i < len(runs):
        kind = run_kind(runs[i])
        if kind is None:
            result.append(runs[i])
            i += 1
            continue
        # Find consecutive runs of same kind
        j = i + 1
        while j < len(runs) and run_kind(runs[j]) == kind:
            j += 1
        count = j - i
        if count >= 2:
            # runs[i] is newest, runs[j-1] is oldest
            result.append({
                "_collapsed": True,
                "kind":  kind,
                "start": runs[j - 1]["start"],  # oldest
                "end":   runs[i]["start"],       # newest
                "count": count,
            })
        else:
            result.append(runs[i])
        i = j
    return result


def render_collapsed_row(item, now, hidden=False):
    """Render a summary <tr> for a collapsed group of runs (issue #17)."""
    start_str = html.escape(fmt_dt_relative(item["start"], now))
    end_str   = html.escape(fmt_dt_relative(item["end"],   now))
    label     = "nothing to do" if item["kind"] == "no_work" else "limit hit"
    count     = item["count"]
    base_cls  = "text-muted" if item["kind"] == "no_work" else "table-danger"
    hidden_cls = " run-hidden d-none" if hidden else ""
    return (
        f'\n      <tr class="{base_cls}{hidden_cls}">'
        f'<td colspan="5" class="text-center small fst-italic">'
        f'between {start_str} and {end_str} \u2014 {label} ({count} runs)'
        f'</td></tr>'
    )


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

    # Group consecutive no-work / limit-hit runs into summary rows (issue #17)
    display_items = group_runs_for_display(runs)
    total = len(display_items)

    if total == 0:
        all_rows = '<tr><td colspan="5" class="text-muted">No runs recorded.</td></tr>'
    else:
        rows_parts = []
        for i, item in enumerate(display_items):
            hidden = i >= SHOW_INITIAL
            if item.get("_collapsed"):
                rows_parts.append(render_collapsed_row(item, now, hidden=hidden))
            else:
                rows_parts.append(render_run_row(item, now, hidden=hidden))
        all_rows = "".join(rows_parts)

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

    # Clone button: opens the clone-commands overlay (issue #16)
    clone_cmds      = proj.get("clone_commands", "")
    create_repo_url = proj.get("create_repo_url", "")
    if clone_cmds:
        cmds_attr   = html.escape(clone_cmds, quote=True)
        create_attr = html.escape(create_repo_url, quote=True)
        clone_btn = (
            f' <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-1 clone-btn"'
            f' data-clone-cmds="{cmds_attr}" data-create-url="{create_attr}"'
            f' title="Show clone commands">clone</button>'
        )
    else:
        clone_btn = ""

    return f"""
  <div class="col-12 col-md-6 col-xxl-4">
    <section class="project-section h-100" id="proj-{proj_id}">
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
          <tbody id="{tbody_id}" data-initial="{SHOW_INITIAL}">{all_rows}
          </tbody>{tfoot_html}
        </table>
      </div>
    </section>
  </div>"""


def render_spawner_html(events):
    """Render a compact spawner events section (issue #15).

    Only shown when spawner-log.yaml has entries (otherwise returns empty string).
    """
    if not events:
        return ""
    # Show most recent 20 events, newest first.
    shown = list(reversed(events[-20:]))
    rows = []
    for e in shown:
        ts  = html.escape(e.get("timestamp", "")[:16].replace("T", " "))
        act = e.get("action", "")
        iss = e.get("issue")
        prj = html.escape(e.get("project", "") or "")
        msg = html.escape(e.get("message", ""))
        badge_cls = (
            "bg-success" if act == "spawned" else
            "bg-danger"  if act == "error"   else
            "bg-secondary"
        )
        issue_link = f' <a href="https://github.com/marenamat/claude-dashboard/issues/{iss}" class="text-muted small">#{iss}</a>' if iss else ""
        rows.append(
            f'<tr>'
            f'<td class="text-nowrap small text-muted">{ts}</td>'
            f'<td><span class="badge {badge_cls}">{html.escape(act)}</span>{issue_link}</td>'
            f'<td class="small">{prj}</td>'
            f'<td class="small">{msg}</td>'
            f'</tr>'
        )
    rows_html = "\n".join(rows)
    return f"""
  <div class="col-12">
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
          <tbody>{rows_html}</tbody>
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

    # Spawner log section (issue #15)
    sections += render_spawner_html(data.get("spawner_events", []))

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
        # Default config goes next to the script (system path not writable without root).
        default_path = SELFDIR / "config.yaml"
        print(f"No config found. Creating default at {default_path}.", file=sys.stderr)
        default_config = {"projects": [{"path": str(SELFDIR), "name": "claude-dashboard"}]}
        default_path.write_text(yaml.dump(default_config, default_flow_style=False))

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
