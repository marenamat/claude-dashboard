import json

# Verify the fix logic: scan log_excerpt for last "result" record,
# check is_error to determine actual limit_hit status.

with open('/home/maria/claude/claude-dashboard/clanker-runs.jsonl') as f:
    lines = f.readlines()

correct = 0
fixed = 0
for i, line in enumerate(lines):
    rec = json.loads(line.strip())
    orig_lh = rec.get('limit_hit', False)

    # Scan log_excerpt for last result record
    actual_error = None
    log = rec.get('log_excerpt', '')
    for l in log.splitlines():
        l = l.strip()
        if not l:
            continue
        try:
            j = json.loads(l)
            if j.get('type') == 'result':
                actual_error = j.get('is_error', False)
        except (json.JSONDecodeError, AttributeError):
            continue

    # If we found a result record
    if actual_error is not None:
        correct_lh = orig_lh and actual_error  # limit_hit only if result was error
        if correct_lh != orig_lh:
            fixed += 1
            if fixed <= 5:
                print(f"Line {i+1}: orig limit_hit={orig_lh} -> corrected to {correct_lh}")
                print(f"  start={rec['start']}, tokens_out={rec.get('tokens_out')}")
        else:
            correct += 1

print(f"\nTotal: {len(lines)}, already correct: {correct}, would fix: {fixed}")
