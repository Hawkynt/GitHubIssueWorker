# Claude Code fixtures

| File | Provenance |
| --- | --- |
| `first-run.ndjson` | Captured from `claude -p --output-format stream-json --verbose --dangerously-skip-permissions --session-id … --json-schema …` (Claude Code 2.1.314, model haiku) on 2026-10-30. Paths sanitised. It carries a real `rate_limit_event` with `status: allowed_warning`, `utilization` and `resetsAt` in epoch **seconds**, and the outcome in `result.structured_output`. |
| `usage-limit.ndjson` | **Not captured** — a limit cannot be provoked on demand. Built from the shapes in the captured run (`rate_limit_event`, `result`) and the rejected-limit messages reported in anthropics/claude-code issues #103820 and #103253 (`api_error: usage_limit_reached`, "You've hit your weekly limit · resets 11am (UTC)"). |
