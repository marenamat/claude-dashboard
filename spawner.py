#!/usr/bin/env python3
# spawner.py: Watch GitHub issues labelled ~SPAWN and create new projects.
# Runs every 5 minutes from crontab.  Pure Python, no external dependencies
# beyond what generate-data.py already uses (cbor2, pyyaml).
#
# Config is read from config.yaml under the "spawner:" key.
# State is kept in spawner-state.yaml (which issues have been processed).
# Events/errors are logged to spawner-log.yaml (read by generate-data.py).

import json
import os
import random
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

SELFDIR      = Path(__file__).parent.resolve()
CONFIG_PATH  = SELFDIR / "config.yaml"
STATE_PATH   = SELFDIR / "spawner-state.yaml"
LOG_PATH     = SELFDIR / "spawner-log.yaml"
SPAWN_LABEL  = "~SPAWN"
# Safe name: lowercase letters, digits, hyphens, underscores only.
RE_SAFE_NAME = re.compile(r'^[a-z0-9_-]+$')
MAX_LOG_ENTRIES = 200


# ---------------------------------------------------------------------------
# GitHub API helpers (same pattern as clanker-prep)
# ---------------------------------------------------------------------------

def _github_token():
    """Read GitHub token from git config github.token."""
    r = subprocess.run(["git", "config", "github.token"],
                       capture_output=True, text=True, cwd=SELFDIR)
    token = r.stdout.strip()
    return token if r.returncode == 0 and token else None


def github_api(path):
    """Fetch from GitHub API. Returns (data, error_string)."""
    url = f"https://api.github.com/{path}"
    headers = {
        "User-Agent": "spawner/1.0",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return None, str(e)


def github_api_post(path, data):
    """POST to GitHub API. Returns (response_data, error_string)."""
    url = f"https://api.github.com/{path}"
    headers = {
        "User-Agent": "spawner/1.0",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state():
    """Load spawner state (set of processed issue numbers)."""
    if not STATE_PATH.exists():
        return {"spawned": {}}
    with open(STATE_PATH) as f:
        data = yaml.safe_load(f) or {}
    # "spawned" maps issue_number → project_name
    return {"spawned": data.get("spawned") or {}}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        yaml.dump(state, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Event log helpers
# ---------------------------------------------------------------------------

def load_log():
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("events", [])


def append_log(events, action, issue_number=None, project=None, message=""):
    """Append an event dict to the log list."""
    events.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action":    action,     # "spawned" | "error" | "skipped"
        "issue":     issue_number,
        "project":   project,
        "message":   message,
    })
    return events


def save_log(events):
    # Keep only the last MAX_LOG_ENTRIES entries.
    trimmed = events[-MAX_LOG_ENTRIES:]
    with open(LOG_PATH, "w") as f:
        yaml.dump({"events": trimmed}, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def add_project_to_config(project_name, project_dir):
    """Add a new project entry to config.yaml if not already present."""
    config = load_config()
    projects = config.get("projects", [])
    for p in projects:
        if p.get("name") == project_name or Path(p.get("path", "")).expanduser() == project_dir:
            return  # already registered
    projects.append({"path": str(project_dir), "name": project_name})
    config["projects"] = projects
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Crontab helpers
# ---------------------------------------------------------------------------

def add_crontab_entry(project_dir, minute):
    """Add two cron entries (at minute M and M+30) for the project's clanker-run."""
    clanker_run = project_dir / "clanker-run"
    entry_comment = f"# clanker {project_dir.name}"
    entry_line    = f"{minute},{minute + 30} * * * * cd {project_dir} && ./clanker-run"

    # Read current crontab (may be empty / not exist yet).
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    # Already present?
    if str(project_dir) in existing:
        return

    new_crontab = existing.rstrip("\n") + f"\n{entry_comment}\n{entry_line}\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)


def pick_cron_minute():
    """Pick a random minute in [7..25] so the second run falls in [37..55]."""
    return random.randint(7, 25)


# ---------------------------------------------------------------------------
# Issue parsing
# ---------------------------------------------------------------------------

def parse_issue(body):
    """
    Parse issue body for:
      Name: <project-name>        (required)
      Upstream: <git-url>         (optional)

    Returns (name, upstream_url) or raises ValueError on bad input.
    """
    name = None
    upstream = None
    for line in (body or "").splitlines():
        line = line.strip()
        m = re.match(r'^Name:\s*(\S+)\s*$', line, re.IGNORECASE)
        if m:
            name = m.group(1).lower()
        m = re.match(r'^Upstream:\s*(\S+)\s*$', line, re.IGNORECASE)
        if m:
            upstream = m.group(1)
    if not name:
        raise ValueError("no 'Name: <project>' line found in issue body")
    if not RE_SAFE_NAME.match(name):
        raise ValueError(f"project name {name!r} contains unsafe characters "
                         "(only lowercase a-z, 0-9, hyphens, underscores allowed)")
    return name, upstream


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def create_project(name, upstream_url, base_dir, claude_base_url, issue_number, issue_data):
    """
    Clone claude-base into base_dir/name, optionally merge upstream, and return
    the project directory path.  Raises on any fatal error.
    """
    project_dir = (base_dir / name).resolve()

    # Guard against re-creation (idempotency safety net)
    if project_dir.exists():
        raise ValueError(f"directory {project_dir} already exists")

    # Clone claude-base
    r = subprocess.run(
        ["git", "clone", "--", claude_base_url, str(project_dir)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed: {r.stderr.strip()}")

    # If upstream supplied, merge it into the clone
    if upstream_url:
        r = subprocess.run(
            ["git", "remote", "add", "upstream", upstream_url],
            capture_output=True, text=True, cwd=project_dir,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git remote add upstream failed: {r.stderr.strip()}")
        r = subprocess.run(
            ["git", "fetch", "upstream"],
            capture_output=True, text=True, cwd=project_dir,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git fetch upstream failed: {r.stderr.strip()}")
        # Merge upstream/main (allow unrelated histories for fresh clones)
        r = subprocess.run(
            ["git", "merge", "--allow-unrelated-histories", "-m",
             "chore: merge upstream into claude-base clone", "upstream/main"],
            capture_output=True, text=True, cwd=project_dir,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git merge upstream failed: {r.stderr.strip()}")

    # Write original issue to claude/design/original-issue.json
    design_dir = project_dir / "claude" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    with open(design_dir / "original-issue.json", "w") as f:
        json.dump(issue_data, f, indent=2, default=str)
        f.write("\n")

    # Commit the original issue file
    subprocess.run(["git", "add", "claude/design/original-issue.json"],
                   cwd=project_dir)
    subprocess.run(
        ["git", "commit", "-m",
         f"chore: record original issue #{issue_number} for {name}"],
        cwd=project_dir,
    )

    return project_dir


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config  = load_config()
    spawner_cfg = config.get("spawner", {})

    repo           = spawner_cfg.get("github_repo", "")
    base_dir       = Path(spawner_cfg.get("base_dir", "~/claude")).expanduser()
    claude_base_url = spawner_cfg.get("claude_base_url",
                                       "https://github.com/marenamat/claude-base.git")

    if not repo:
        print("spawner: no github_repo in config.yaml spawner section", file=sys.stderr)
        sys.exit(1)

    state  = load_state()
    events = load_log()

    # Fetch all open issues from the dashboard repo
    issues, err = github_api(f"repos/{repo}/issues?state=open&per_page=100")
    if err:
        events = append_log(events, "error", message=f"GitHub issues fetch failed: {err}")
        save_log(events)
        print(f"spawner: issues fetch error: {err}", file=sys.stderr)
        sys.exit(1)

    # Filter for ~SPAWN labelled issues not yet processed
    spawn_issues = [
        i for i in issues
        if any(lb["name"] == SPAWN_LABEL for lb in i.get("labels", []))
        and i["number"] not in state["spawned"]
    ]

    if not spawn_issues:
        print("spawner: nothing to spawn.", file=sys.stderr)
        save_log(events)
        return

    for issue in spawn_issues:
        num   = issue["number"]
        title = issue["title"]
        body  = issue.get("body") or ""
        print(f"spawner: processing issue #{num}: {title}", file=sys.stderr)

        # Parse issue body
        try:
            name, upstream_url = parse_issue(body)
        except ValueError as e:
            msg = f"issue #{num} parse error: {e}"
            print(f"spawner: {msg}", file=sys.stderr)
            events = append_log(events, "error", issue_number=num, message=msg)
            continue

        # Create the project
        try:
            project_dir = create_project(
                name          = name,
                upstream_url  = upstream_url,
                base_dir      = base_dir,
                claude_base_url = claude_base_url,
                issue_number  = num,
                issue_data    = {
                    "number":     num,
                    "title":      title,
                    "body":       body,
                    "created_at": issue.get("created_at"),
                    "labels":     [lb["name"] for lb in issue.get("labels", [])],
                },
            )
        except Exception as e:
            msg = f"issue #{num} project creation failed: {e}"
            print(f"spawner: {msg}", file=sys.stderr)
            events = append_log(events, "error", issue_number=num, project=name, message=msg)
            continue

        # Register the project in the dashboard config
        try:
            add_project_to_config(name, project_dir)
        except Exception as e:
            msg = f"issue #{num} config update failed: {e}"
            print(f"spawner: {msg}", file=sys.stderr)
            events = append_log(events, "error", issue_number=num, project=name, message=msg)
            # Project is created; record it anyway so we don't retry endlessly.
            state["spawned"][num] = name
            save_state(state)
            continue

        # Add crontab entry
        minute = pick_cron_minute()
        try:
            add_crontab_entry(project_dir, minute)
        except Exception as e:
            msg = f"issue #{num} crontab failed: {e}"
            print(f"spawner: {msg}", file=sys.stderr)
            events = append_log(events, "error", issue_number=num, project=name, message=msg)
            # Non-fatal: project exists, just no cron yet.

        # Post a comment back to the issue confirming the spawn
        comment_body = (
            f"Project `{name}` spawned at `{project_dir}`.\n\n"
            f"Cron: `{minute},{minute + 30} * * * *`\n\n"
            "The project has been added to the dashboard and will start running soon."
        )
        _, cerr = github_api_post(
            f"repos/{repo}/issues/{num}/comments",
            {"body": comment_body},
        )
        if cerr:
            print(f"spawner: failed to post comment on issue #{num}: {cerr}", file=sys.stderr)

        # Mark as spawned
        state["spawned"][num] = name
        save_state(state)
        events = append_log(events, "spawned", issue_number=num, project=name,
                            message=f"spawned at {project_dir}")
        print(f"spawner: spawned {name} from issue #{num}", file=sys.stderr)

    save_log(events)


if __name__ == "__main__":
    main()
