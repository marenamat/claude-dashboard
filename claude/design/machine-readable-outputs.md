# Machine-readable outputs from clanker-run and clanker-prep

Requirements for the `claude-base` repository (issue #6).

The dashboard currently scrapes unstructured text from `clanker.log` using
regex patterns.  This is fragile.  The goal is to have `clanker-run` and
`clanker-prep` emit structured records that the dashboard can parse reliably.

---

## 1. Run record emitted by `clanker-run`

After each invocation attempt (whether Claude was called or not), append one
JSON line to a machine-readable sidecar file, e.g. `clanker-runs.jsonl`.

### Required fields

```json
{
  "start":       "2026-04-06T21:00:00+00:00",
  "end":         "2026-04-06T21:04:12+00:00",
  "invoked":     true,
  "exit_code":   0,
  "cost_usd":    0.1234,
  "tokens_in":   12345,
  "tokens_out":  678,
  "limit_hit":   false,
  "log_excerpt": "last 40 lines of run output"
}
```

### Field notes

- **start / end**: ISO 8601 with timezone offset.
- **invoked**: `true` if `claude` was actually called this run.
- **exit_code**: exit code of the `claude` process (or -1 if not invoked).
- **cost_usd / tokens_in / tokens_out**: extracted from `--output-format=stream-json`
  (see §3 below); `null` if not available.
- **limit_hit**: `true` if "You've hit your limit" was detected in output.
- **log_excerpt**: last ≤40 lines of the combined prep+run output as a single string
  with `\n` line separators.

---

## 2. Getting token/cost data with `--output-format=stream-json`

Running `claude --output-format=stream-json` makes the CLI emit one JSON
object per line on stdout.  The final object is a `result` message that
includes usage statistics.

Example structure (subject to change across Claude Code versions):

```json
{"type":"result","subtype":"success","cost_usd":0.1234,
 "usage":{"input_tokens":12345,"output_tokens":678,...}}
```

`clanker-run` should:
1. Invoke Claude with `--output-format=stream-json`.
2. Tee the raw stream to the existing `clanker.log` (for human reading) and
   also consume it line-by-line.
3. Parse each line as JSON; on the `result` message, extract `cost_usd` and
   `usage.input_tokens` / `usage.output_tokens`.
4. Write extracted values into the run record (§1).

If parsing fails (future CLI format change), fall back to `null` for those
fields — never crash the wrapper.

---

## 3. Prep summary emitted by `clanker-prep`

`clanker-prep` currently writes human-readable lines to the log.  Add a
machine-readable summary block at the end of each prep run, written to
`clanker-prep.json` (overwritten each run, not appended):

```json
{
  "recorded_at": "2026-04-06T21:00:00+00:00",
  "decision":    "INVOKE_CLAUDE",
  "reasons":     ["new commits on main", "open issue #4"],
  "fetched_issues": [4, 5, 6],
  "fetched_pipelines": [24047283931, 24047283890],
  "git_actions": ["fetched", "template_up_to_date"]
}
```

- **decision**: `"INVOKE_CLAUDE"` or `"SKIP"`.
- **reasons**: human-readable list of why that decision was taken.
- **fetched_issues** / **fetched_pipelines**: IDs seen this run.
- **git_actions**: list of actions taken (matches existing `git_status.yaml`).

The dashboard can show the prep decision and reasons alongside each run.

---

## 4. Rate-limit detection

The current text match `"you've hit your limit"` (case-insensitive) is
opportunistic.  With `--output-format=stream-json`, a more reliable approach:

- If the `result` object has `"subtype": "error"` and the error message
  contains "limit", set `limit_hit = true`.
- Keep the text fallback for compatibility with older CLI versions.

---

## 5. `clanker.log` format — no change required

The existing human-readable `clanker.log` (with `===========` separators and
`date` lines) should be kept as-is for human inspection.  The new
`.jsonl`/`.json` sidecars are additive.

---

## 6. Dashboard integration

Once claude-base implements the above, the dashboard's `generate-data.py`
should:

1. Read `clanker-runs.jsonl` if present (structured, preferred).
2. Fall back to parsing `clanker.log` if the sidecar is absent (existing
   behaviour, kept for backward compatibility with old deployments).
3. Optionally read `clanker-prep.json` and surface the prep decision/reasons
   in the run card.
