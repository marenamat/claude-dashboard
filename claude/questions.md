# Questions / Dependency Requests

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
