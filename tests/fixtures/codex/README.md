# Codex fixtures

| File | Provenance |
| --- | --- |
| `first-run.ndjson` | Captured from `codex exec --json --dangerously-bypass-approvals-and-sandbox --output-schema … -o …` (codex-cli 0.166.0, stdin closed) on 2026-10-30. Profile and work paths replaced by `/home/user/.codex` and `/work/issue-42`. |
| `resume.ndjson` | Captured from `codex exec resume <thread> --json … -` with the prompt on stdin, same session, same day and version. |
| `usage-limit.ndjson` | **Not captured** — a limit cannot be provoked on demand. Built from the message format reported in openai/codex issues #22570 and #21073 (an `error` and a `turn.failed` event carrying "You've hit your usage limit … try again at Mar 20th, 2027 3:36 PM."). Replace with a real capture when one occurs. |
