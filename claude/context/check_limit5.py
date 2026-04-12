import json

with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    lines = f.readlines()

# Look at a few recent invoked=True, limit_hit=True lines more carefully
# Lines 214, 215, 220, 221, 225 from the file (1-indexed)
# These have tokens, so perhaps actually completed successfully
targets = [214, 215, 220, 221, 225]
for t in targets:
    rec = json.loads(lines[t-1].strip())
    print(f"Line {t}: start={rec['start']}, end={rec.get('end')}, invoked={rec['invoked']}, limit_hit={rec['limit_hit']}")
    print(f"  cost={rec.get('cost_usd')}, tokens_in={rec.get('tokens_in')}, tokens_out={rec.get('tokens_out')}")
    log = rec.get('log_excerpt', '')
    print(f"  log (last 3 lines):")
    for l in log.splitlines()[-3:]:
        print(f"    {repr(l[:200])}")
    print()
