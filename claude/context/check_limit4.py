import json

with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    lines = f.readlines()

# Find invoked=True, limit_hit=True runs that also have cost/tokens
print("Invoked+limit_hit runs WITH cost/tokens (successful despite limit?):")
count = 0
for i, line in enumerate(lines):
    rec = json.loads(line.strip())
    if rec.get('invoked') and rec.get('limit_hit'):
        cost = rec.get('total_cost_usd') or rec.get('cost_usd')
        tin = rec.get('tokens_in')
        tout = rec.get('tokens_out')
        if cost or tin or tout:
            count += 1
            if count <= 5:
                print(f"  Line {i+1}: cost={cost}, tokens_in={tin}, tokens_out={tout}")

print(f"Total: {count}")

# Look at what fields a JSONL line actually has
print()
print("Fields in first few records:")
for i, line in enumerate(lines[:3]):
    rec = json.loads(line.strip())
    print(f"  Line {i+1}: {sorted(rec.keys())}")
