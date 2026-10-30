"""Google Antigravity (``agy``) as an agent (F12.5).

Verified against agy 1.4.11 (``tests/fixtures/antigravity``): ``-p <prompt> --output-format
stream-json`` emits an ``init`` with the ``conversation_id``, ``step_update`` events, and a
``result`` with ``status`` and ``usage``. With stdin closed it produced complete output without a
pseudo-terminal. The prompt is an argument, so it gets a pointer to ``.ghiw/prompt.md``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.ports.agents import (
    PROMPT_FILE,
    AgentRun,
    Invocation,
    RunReport,
    TokenUsage,
    UsageLimit,
)

_LIMIT = re.compile(r"quota|rate.?limit|resource.?exhausted|usage limit|\b429\b", re.IGNORECASE)
_POINTER = (
    f"Read the file {PROMPT_FILE.as_posix()} in the current directory and carry out every "
    "instruction in it."
)


class AntigravityAdapter:
    kind = "antigravity"

    def __init__(self, executable: str, env: dict[str, str] | None = None) -> None:
        self._executable = executable
        self._env = env or {}

    def invocation(self, run: AgentRun) -> Invocation:
        prepare(run)
        argv = [self._executable, "-p", _POINTER, "--output-format", "stream-json"]
        argv.append("--dangerously-skip-permissions")
        if run.session:
            argv += ["--conversation", run.session]
        if run.model:
            argv += ["--model", run.model]
        return Invocation(argv=argv, cwd=run.worktree, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        session: str | None = None
        result: dict[str, Any] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            session = event.get("conversation_id") or session
            if event.get("event") == "result":
                result = event.get("result") or {}
                session = result.get("conversation_id") or session

        tokens = result.get("usage") or {}
        usage = (
            TokenUsage(int(tokens.get("input_tokens", 0)), int(tokens.get("output_tokens", 0)))
            if tokens
            else None
        )
        if result.get("status") != "SUCCESS" or exit_code != 0:
            message = str(result.get("response") or stderr.strip() or f"exit {exit_code}")
            if _LIMIT.search(message):
                return RunReport(session, None, limit=UsageLimit(message, None), usage=usage)
            return RunReport(session, None, usage=usage, error=message)

        outcome, problem = read_outcome(run.worktree)
        return RunReport(session, outcome, usage=usage, error=problem)
