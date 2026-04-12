import json

with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    lines = f.readlines()

# Check combinations of invoked and limit_hit
combos = {}
for line in lines:
    rec = json.loads(line.strip())
    inv = rec.get('invoked', False)
    lh = rec.get('limit_hit', False)
    key = (inv, lh)
    combos[key] = combos.get(key, 0) + 1

print("invoked / limit_hit combinations:")
for (inv, lh), count in sorted(combos.items()):
    print(f"  invoked={inv}, limit_hit={lh}: {count} runs")

# Show a few not-invoked but limit_hit=True
print()
print("Sample non-invoked runs with limit_hit=True:")
count = 0
for i, line in enumerate(lines):
    rec = json.loads(line.strip())
    if not rec.get('invoked') and rec.get('limit_hit') and count < 3:
        count += 1
        log = rec.get('log_excerpt', '')
        print(f"  Line {i+1}: start={rec.get('start')}")
        for l in log.splitlines():
            if 'limit' in l.lower() or 'rate' in l.lower() or 'invoke' in l.lower():
                print(f"    {repr(l[:150])}")
