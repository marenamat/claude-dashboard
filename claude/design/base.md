# Dashboard

There are multiple projects on a single machine, running the framework
from `claude-base`. Create a read-only dashboard, as a locally running web app,
showing:

- token spendings
- time of run
- recent logs

All of that separated per every project / claude instance.

## Nginx binding

Prepare a set of configuration files to allow serving the dashboard via Nginx
running on a different machine.
