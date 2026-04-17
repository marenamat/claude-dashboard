#!/usr/bin/env python3
# ws-server.py: WebSocket server for Claude Dashboard autoreload (issue #9).
# Watches www/data.cbor and project clanker.lock files for changes.
# Broadcasts JSON messages to connected browser clients so they reload
# automatically and show which clanker instances are currently running.
#
# Requires: websockets >= 12 (pip install websockets)
# Listens on: [::]:8043 (proxied by nginx at /ws)
#
# Message types sent to clients:
#   {"type": "data-updated"}
#       data.cbor changed; client should re-fetch and re-render
#   {"type": "running", "name": "<project>", "running": true/false}
#       clanker.lock appeared or disappeared for a project

import asyncio
import json
import logging
import sys
import yaml
from pathlib import Path

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("ERROR: websockets package not installed. Run: pip install websockets", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SELFDIR = Path(__file__).parent.resolve()
# Config search order: system-wide path first, then script directory.
_SYSTEM_CONFIG = Path("/etc/claude-dashboard/config.yaml")
CONFIG_PATH = _SYSTEM_CONFIG if _SYSTEM_CONFIG.exists() else SELFDIR / "config.yaml"
WWW = SELFDIR / "www"

WS_HOST = "::"          # all IPv6 interfaces (dual-stack covers IPv4 too)
WS_PORT = 8043
POLL_INTERVAL = 2.0     # seconds between mtime polls

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ws-server")

CLIENTS: set = set()


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------

async def broadcast(msg: str) -> None:
    """Send msg to all connected clients; silently remove dead ones."""
    if not CLIENTS:
        return
    dead = set()
    for ws in list(CLIENTS):
        try:
            await ws.send(msg)
        except Exception:
            dead.add(ws)
    CLIENTS.difference_update(dead)


# ---------------------------------------------------------------------------
# WebSocket connection handler
# ---------------------------------------------------------------------------

async def handler(ws) -> None:
    """Accept one WebSocket connection and hold it until the client disconnects."""
    CLIENTS.add(ws)
    log.info("client connected  (total=%d  peer=%s)", len(CLIENTS), ws.remote_address)
    try:
        # Send current running state immediately so a freshly loaded page is
        # up-to-date without waiting for the next poll tick.
        await ws.wait_closed()
    finally:
        CLIENTS.discard(ws)
        log.info("client disconnected (total=%d)", len(CLIENTS))


# ---------------------------------------------------------------------------
# File watcher
# ---------------------------------------------------------------------------

async def watch(config: dict) -> None:
    """Poll data.cbor mtime and per-project clanker.lock; broadcast changes."""
    data_cbor = WWW / "data.cbor"
    projects = config.get("projects", [])

    last_data_mtime: float | None = None
    # name -> bool (True = running)
    last_lock: dict[str, bool] = {}

    while True:
        await asyncio.sleep(POLL_INTERVAL)

        # Check data.cbor modification time
        try:
            mtime = data_cbor.stat().st_mtime
        except OSError:
            mtime = None

        if mtime is not None:
            if last_data_mtime is not None and mtime != last_data_mtime:
                log.info("data.cbor changed — broadcasting data-updated")
                await broadcast(json.dumps({"type": "data-updated"}))
            last_data_mtime = mtime

        # Check lock files for each configured project
        for entry in projects:
            path = Path(entry["path"]).expanduser()
            name = entry.get("name") or path.name
            running = (path / "clanker.lock").exists()
            if last_lock.get(name) != running:
                last_lock[name] = running
                log.info("project %r running=%s", name, running)
                await broadcast(json.dumps({
                    "type": "running",
                    "name": name,
                    "running": running,
                }))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    if not CONFIG_PATH.exists():
        log.error("Config not found: %s", CONFIG_PATH)
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    log.info("Starting WebSocket server on [%s]:%d", WS_HOST, WS_PORT)

    async with serve(handler, WS_HOST, WS_PORT) as server:
        log.info("Listening — waiting for dashboard clients")
        await asyncio.gather(
            server.serve_forever(),
            watch(config),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped.")
