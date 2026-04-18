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

