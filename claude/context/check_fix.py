from pathlib import Path
import importlib.util
spec = importlib.util.spec_from_file_location("gd", "/home/maria/claude/claude-dashboard/generate-data.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)

runs = gd.parse_jsonl(Path('/home/maria/claude/claude-dashboard/clanker-runs.jsonl'))

lh_true = sum(1 for r in runs if r['limit_hit'])
lh_false = sum(1 for r in runs if not r['limit_hit'])
has_reset = sum(1 for r in runs if r['limit_reset'])

print(f"Total runs: {len(runs)}")
print(f"limit_hit=True: {lh_true}")
print(f"limit_hit=False: {lh_false}")
print(f"has limit_reset: {has_reset}")

print()
print("Sample limit_hit runs with reset times:")
for r in runs:
    if r['limit_hit'] and r['limit_reset']:
        print(f"  start={r['start']}, limit_reset={r['limit_reset']}")
        break

print("Sample limit_hit runs WITHOUT reset times:")
for r in runs:
    if r['limit_hit'] and not r['limit_reset']:
        print(f"  start={r['start']}, limit_reset={r['limit_reset']}")
        break
