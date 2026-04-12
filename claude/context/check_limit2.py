import json

# Check: does clanker-runs.jsonl actually have limit_hit field in the JSON?
# Or is parse_jsonl relying on something else?
with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    lines = f.readlines()

limit_hit_true = 0
limit_hit_false = 0
no_limit_field = 0
has_limit_reset = 0

for i, line in enumerate(lines):
    rec = json.loads(line.strip())
    lh = rec.get('limit_hit')
    if lh is True:
        limit_hit_true += 1
    elif lh is False:
        limit_hit_false += 1
    else:
        no_limit_field += 1
    if rec.get('limit_reset'):
        has_limit_reset += 1

print(f"Total lines: {len(lines)}")
print(f"limit_hit=True: {limit_hit_true}")
print(f"limit_hit=False: {limit_hit_false}")
print(f"no limit_hit field: {no_limit_field}")
print(f"has limit_reset: {has_limit_reset}")

# Show a few with limit_hit=True and their log excerpt context
print()
print("Sample limit_hit=True runs:")
count = 0
for i, line in enumerate(lines):
    rec = json.loads(line.strip())
    if rec.get('limit_hit') is True and count < 3:
        count += 1
        print(f"  Line {i+1}: start={rec.get('start')}, invoked={rec.get('invoked')}")
        log = rec.get('log_excerpt', '')
        for l in log.splitlines():
            if 'limit' in l.lower() or 'rate' in l.lower():
                # truncate long lines
                print(f"    {repr(l[:200])}")
