# Dashboard

There are multiple projects on a single machine, running the framework
from `claude-base`. Create a read-only dashboard, as a locally running web app,
showing:

- token spendings
- time of run
- recent logs

All of that separated per every project / claude instance.

## Nginx binding

Two-machine topology (issue #2):

- **Clankers machine** (sysvinit, no systemd): runs a dedicated nginx instance
  serving `www/` over plain HTTP on the local network (port 8042).
  Managed by the sysvinit init script at `etc/init.d/claude-dashboard`.
  Config: `nginx/clanker.conf`.

- **Proxy machine**: runs nginx exposed to the internet, terminates HTTPS,
  and reverse-proxies to the clankers machine over the local network.
  Config: `nginx/proxy.conf` (drop into `/etc/nginx/sites-enabled/`).

# GitHub Actions

GitHub Pages should be used for dashboard demonstration purposes with mock backend data.
