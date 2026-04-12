import json
import sys

with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    for i, line in enumerate(f):
        rec = json.loads(line)
        if rec.get('limit_hit'):
            print(f'Line {i+1}: limit_hit={rec["limit_hit"]}, limit_reset={rec.get("limit_reset")}')
            log = rec.get('log_excerpt', '')
            for l in log.splitlines():
                if 'limit' in l.lower():
                    print(f'  log: {repr(l)}')
        # Also check for limit_reset without limit_hit
        if rec.get('limit_reset') and not rec.get('limit_hit'):
            print(f'Line {i+1}: BUG? limit_reset={rec["limit_reset"]} but limit_hit={rec.get("limit_hit")}')
