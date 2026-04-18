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

**Note**: A bug in the APKBUILD was fixed (issue #9) — previously the package
installed `nginx/clanker.conf` (the standalone full-nginx config with an
`http {}` wrapper) instead of `packaging/clanker.conf` (the http.d snippet).
Copying a full nginx config into `/etc/nginx/http.d/` breaks nginx (nested
`http {}` blocks) and caused the 426 Upgrade Required WebSocket error.

After installing the updated APK:

1. Copy the nginx http.d snippet and reload nginx:

```
cp /usr/share/claude-dashboard/nginx/clanker.conf /etc/nginx/http.d/
rc-service nginx reload
```

2. Enable and start the WebSocket autoreload service:

```
rc-update add claude-dashboard-ws
rc-service claude-dashboard-ws start
```

3. Create `/etc/claude-dashboard/config.yaml` with your project paths if not
   done yet (the cron will generate data automatically every 15 minutes).

The APK package already provides `py3-websockets` as a dependency, so no
manual pip/apt install is needed.

*The websocket still fails, the nginx config was not the problem.*

---

## CSP inline script violation (issue #9)

After the WS nginx config fix, a browser CSP error appeared:

```
Content-Security-Policy: The page's settings blocked an inline script
(script-src-elem) from being executed because it violates the following
directive: "script-src 'self' 'wasm-unsafe-eval'".
Consider using a hash ('sha256-nCwXOGJ72MPizaTB2tzvBjGKo7v92xgmtZfSCjZwCWg=')
or a nonce.
```

The current CSP in `packaging/clanker.conf` is:
```
script-src 'self' 'wasm-unsafe-eval'
```

We cannot find any inline `<script>` in our code (index.template.html, dashboard.js,
generate-data.py, src/lib.rs). The hash suggests the browser is blocking a real
inline script element.

**Guardian: please check the following and report back:**

1. Does this error appear on a fresh page load (no browser extensions)?
2. Open browser DevTools → Sources → look for an inline script. What file/context
   is it in?
3. Does it appear when accessing `localhost:8042` directly (no HTTPS proxy)?
4. Does the page still work despite the error, or is something broken?

If the hash is stable across reloads, we can whitelist it in the CSP as a
temporary fix while investigating the source.

*Page works, and the bug is from a browser extension. But the autoreload / WS still doesn't.*
