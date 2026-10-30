"""Claude Code as an agent (F12.3), directly or through ClaudeLocal (F15.4).

Verified against Claude Code 2.1.314 (``tests/fixtures/claude``): ``-p --output-format stream-json
--verbose`` emits a ``system/init`` and a ``result`` carrying ``session_id``; ``--json-schema`` puts
the outcome into ``result.structured_output``; ``total_cost_usd`` is the run's cost; and every run
carries a ``rate_limit_event`` whose ``status`` is ``allowed``, ``allowed_warning`` or ``rejected``,
with ``utilization`` and ``resetsAt`` in epoch seconds. The prompt is read from stdin.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.domain.outcome import OUTCOME_SCHEMA
from github_issue_worker.ports.agents import (
    OUTCOME_FILE,
    AgentRun,
    Invocation,
    RunReport,
    TokenUsage,
    UsageLimit,
)

_SETTINGS = json.dumps(
    {
        "includeCoAuthoredBy": False,
        "permissions": {"deny": ["Bash(git push:*)", "Bash(gh:*)"]},
    }
)
"""No co-author trailer, and deny rules that hold even with permission prompts off."""


def _events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _write_structured_outcome(run: AgentRun, structured: Any) -> None:
    path = run.worktree / OUTCOME_FILE
    if isinstance(structured, dict) and not path.exists():
        path.write_text(json.dumps(structured), encoding="utf-8")


def _interpret(
    run: AgentRun, events: list[dict[str, Any]], exit_code: int, stderr: str
) -> RunReport:
    session: str | None = None
    usage: TokenUsage | None = None
    utilization: float | None = None
    rejected_reset: datetime | None = None
    limited = False
    failure: str | None = None

    for event in events:
        session = event.get("session_id") or session
        kind = event.get("type")
        if kind == "rate_limit_event":
            info = event.get("rate_limit_info") or {}
            if isinstance(info.get("utilization"), int | float):
                utilization = float(info["utilization"])
            if info.get("status") == "rejected":
                limited = True
                if isinstance(info.get("resetsAt"), int | float):
                    rejected_reset = datetime.fromtimestamp(info["resetsAt"], tz=UTC)
        elif event.get("api_error") == "usage_limit_reached":
            limited = True
        if kind == "result":
            _write_structured_outcome(run, event.get("structured_output"))
            cost = event.get("total_cost_usd")
            tokens = event.get("usage") or {}
            usage = TokenUsage(
                input_tokens=int(tokens.get("input_tokens", 0)),
                output_tokens=int(tokens.get("output_tokens", 0)),
                cost_usd=float(cost) if isinstance(cost, int | float) else None,
            )
            if event.get("is_error"):
                failure = str(event.get("result") or event.get("subtype") or "error")

    if limited:
        message = "usage limit reached"
        return RunReport(
            session=session,
            outcome=None,
            limit=UsageLimit(message=message, reset_at=rejected_reset),
            usage=usage,
            utilization=utilization,
        )

    outcome, problem = read_outcome(run.worktree)
    if exit_code != 0 or failure or not events:
        reason = failure or stderr.strip() or f"exit {exit_code}"
        return RunReport(session, outcome, usage=usage, error=reason, utilization=utilization)
    return RunReport(session, outcome, usage=usage, error=problem, utilization=utilization)


class ClaudeAdapter:
    kind = "claude"

    def __init__(self, executable: str, env: dict[str, str] | None = None) -> None:
        self._executable = executable
        self._env = env or {}

    def invocation(self, run: AgentRun) -> Invocation:
        prepare(run)
        argv = [
            self._executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--settings",
            _SETTINGS,
            "--json-schema",
            json.dumps(OUTCOME_SCHEMA),
        ]
        argv += ["--resume", run.session] if run.session else ["--session-id", str(uuid.uuid4())]
        if run.model:
            argv += ["--model", run.model]
        return Invocation(argv=argv, cwd=run.worktree, stdin=run.prompt, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        return _interpret(run, _events(stdout), exit_code, stderr)


class ClaudeLocalAdapter:
    """ClaudeLocal's own Claude Code on a local model, through its launcher.

    ``claude.sh --dir <worktree> --prompt -`` continues the newest session of that directory or
    starts one, reads the prompt from stdin and exits; the worktree is per issue, so its session is
    too. It prints one JSON result, and runs without permission prompts by default.
    """

    kind = "claude"

    def __init__(self, launcher: Path, env: dict[str, str] | None = None) -> None:
        self._launcher = launcher
        self._env = env or {}

    def invocation(self, run: AgentRun) -> Invocation:
        prepare(run)
        argv = [str(self._launcher), "--dir", str(run.worktree), "--prompt", "-"]
        argv += ["--output-format", "json"]
        if run.model:
            argv += ["--model", run.model]
        return Invocation(argv=argv, cwd=run.worktree, stdin=run.prompt, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        return _interpret(run, _events(stdout), exit_code, stderr)
