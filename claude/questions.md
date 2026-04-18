# Questions / Dependency Requests

## Required setup for issue-15 (Project spawner)

`spawner.py` is ready to deploy.  To activate it:

1. Add a `spawner:` section to `config.yaml`:

```yaml
spawner:
  github_repo: marenamat/claude-dashboard
  base_dir: ~/claude
  claude_base_url: https://github.com/marenamat/claude-base.git
```

2. Add a crontab entry to run it every 5 minutes:

```
*/5 * * * * cd /home/maria/claude/claude-dashboard && python3 spawner.py >> /tmp/spawner.log 2>&1
```

3. Label the target issue(s) with `~SPAWN` on GitHub.

---

## WebSocket autoreload still failing (issue #9)

The APKBUILD clanker.conf path fix and the IPv4→IPv6 proxy fix are now committed.
The CSP error was confirmed to be a browser extension (no action needed).

But the WebSocket still does not work.  To help diagnose, please check and
report back:

1. **Is ws-server running?**

   ```sh
   ps aux | grep ws-server
   ```

2. **Does the log show any startup error?**

   ```sh
   cat /var/log/claude-dashboard/ws-server.log
   ```

   (or wherever it was started from — check `$LOGFILE` in your init script)

3. **Can you connect directly to port 8043 with curl?**

   ```sh
   curl -v --http1.1 -H "Upgrade: websocket" -H "Connection: Upgrade" \
        http://[::1]:8043/
   ```

   Expected: `101 Switching Protocols`.  If you get 426, the server is up but
   not getting the Upgrade header.  If connection refused, the server is not
   listening.

4. **What does nginx return for /ws without the proxy?**

   Access port 8042 directly from the clanker machine:

   ```sh
   curl -v --http1.1 -H "Upgrade: websocket" -H "Connection: Upgrade" \
        http://[::1]:8042/ws
   ```

   Expected: `101 Switching Protocols` (nginx passes it through to ws-server).

---

## Required setup for issue-9 (Autoreload WebSocket server)

After installing the updated APK (or running from the dev tree):

1. Copy the nginx http.d snippet and reload nginx (APK install only):

```sh
cp /usr/share/claude-dashboard/nginx/clanker.conf /etc/nginx/http.d/
rc-service nginx reload
```

2. Enable and start the WebSocket autoreload service:

```sh
rc-update add claude-dashboard-ws
rc-service claude-dashboard-ws start
```

3. Create `/etc/claude-dashboard/config.yaml` with your project paths.
   The server now starts even without this file (logs a warning), but
   lock-file monitoring won't work until the file exists.

The APK package already provides `py3-websockets` as a dependency, so no
manual pip/apt install is needed.

---

## Dependency for issue-22 (Denied permissions: show full commands)

Issue #22 asks to show the full denied tool calls (e.g. `Bash(ls /foo)` instead
of just `Bash`) when permissions are denied.

The dashboard displays log excerpts verbatim from `clanker.log`. What gets
logged is whatever Claude Code CLI outputs. Currently the CLI only prints the
tool name, not its arguments, when rejecting a tool call.

To fix this, one of the following must happen **in `claude-base` (clanker-run)**:

- When running with `--output-format=stream-json`, Claude Code emits one JSON
  object per tool call attempt. Denied calls appear in the stream and include
  the full `input` object. `clanker-run` could detect these events and write
  a line like `Denied: Bash(ls /foo)` to the log before Claude Code's own
  summary.

Until that is done, the dashboard has nothing extra to display.
