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

## Required package for issue-9 (Autoreload WebSocket server)

`ws-server.py` uses the `websockets` Python library (async WebSocket server).

```
pip install websockets
```

Or via system package if available:

```
apt install python3-websockets
```

After installing, enable and start the new sysvinit service:

```
chmod +x /home/maria/claude/claude-dashboard/etc/init.d/claude-dashboard-ws
cp etc/init.d/claude-dashboard-ws /etc/init.d/
update-rc.d claude-dashboard-ws defaults
service claude-dashboard-ws start
```

Also reload nginx after updating `nginx/clanker.conf`:

```
nginx -s reload -c /path/to/nginx/clanker.conf
```
