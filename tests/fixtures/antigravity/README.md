# Antigravity fixtures

| File | Provenance |
| --- | --- |
| `first-run.ndjson` | Captured from `agy -p … --output-format stream-json --dangerously-skip-permissions` (agy 1.4.11, ClaudeCodePortable profile with `HOME` redirected) on 2026-10-30, stdin closed and **no** pseudo-terminal: the reported non-TTY output loss did not reproduce with this version. Paths sanitised. |
| `usage-limit.ndjson` | Terminal event from the real 2026-11-01 07:56 UTC continuation of issue 78. The structured `error` carries the vendor's `126h12m53s` countdown; `response` is empty. Earlier init and step events omitted. |
