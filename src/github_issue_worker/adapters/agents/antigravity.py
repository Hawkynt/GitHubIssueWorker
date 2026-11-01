"""Google Antigravity (``agy``) as an agent (F12.5).

Verified against agy 1.4.11 (``tests/fixtures/antigravity``): ``-p <prompt> --output-format
stream-json`` emits an ``init`` with the ``conversation_id``, ``step_update`` events, and a
``result`` with ``status`` and ``usage``. With stdin closed it produced complete output without a
pseudo-terminal. The prompt is an argument, so it gets a pointer to ``.ghiw/prompt.md``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.ports.agents import (
    PROMPT_FILE,
    AgentRun,
    Invocation,
    RunObservation,
    RunReport,
    TokenUsage,
    UsageLimit,
)

_LABEL = re.compile(r'model override to backend: label="([^"]+)"')
_LIMIT = re.compile(r"quota|rate.?limit|resource.?exhausted|usage limit|\b429\b", re.IGNORECASE)
_RESET = re.compile(
    r"\bResets in (?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?"
    r"(?=[.\s\"\'}]|$)",
    re.IGNORECASE,
)
_POINTER = (
    f"Read the file {PROMPT_FILE.as_posix()} in the current directory and carry out every "
    "instruction in it."
)


class AntigravityAdapter:
    kind = "antigravity"

    def __init__(
        self,
        executable: str,
        env: dict[str, str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._executable = executable
        self._env = env or {}
        self._now = now or (lambda: datetime.now(UTC))

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
        observed = self.observe(run, stdout)
        report = self._report(run, exit_code, stdout, stderr)
        return replace(
            report,
            model=observed.models[-1] if observed.models else None,
            models=observed.models,
            worked=observed.worked or (report.limit is None and report.outcome is not None),
            usage=report.usage if observed.worked or report.limit is None else None,
        )

    def observe(self, run: AgentRun, stdout: str) -> RunObservation:
        session = run.session
        worked = False
        for event in _events(stdout):
            session = event.get("conversation_id") or session
            result = event.get("result") or {}
            session = result.get("conversation_id") or session
            step = event.get("step_update") or {}
            if step.get("step_type") in ("agent_response", "tool"):
                worked = True
        model = self._model(session)
        return RunObservation(session, (model,) if model else (), worked)

    def _model(self, session: str | None) -> str | None:
        """Only the log of this conversation can identify its model under concurrent runs."""
        if not session:
            return None
        home = Path(self._env.get("HOME") or Path.home())
        logs = sorted((home / ".gemini" / "antigravity-cli" / "log").glob("cli-*.log"))
        for path in reversed(logs):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if (
                f"Print mode: resuming conversation {session}" in text
                or f"Print mode: conversation={session}," in text
            ):
                found = _LABEL.findall(text)
                return found[-1] if found else None
        return None

    def _report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        session: str | None = None
        result: dict[str, Any] = {}
        steps: dict[int, dict[str, Any]] = {}
        for index, event in enumerate(_events(stdout)):
            session = event.get("conversation_id") or session
            step = event.get("step_update") or {}
            if step.get("usage"):
                steps[step.get("step_index", index)] = step["usage"]
            if event.get("event") == "result":
                result = event.get("result") or {}
                session = result.get("conversation_id") or session

        tokens = result.get("usage") or {}
        if steps:
            tokens = {
                name: sum(int(usage.get(name, 0)) for usage in steps.values())
                for name in ("input_tokens", "output_tokens")
            }
        elif run.session:
            # Continuation results report the whole conversation, even for an immediate 429.
            tokens = {}
        usage = (
            TokenUsage(int(tokens.get("input_tokens", 0)), int(tokens.get("output_tokens", 0)))
            if tokens
            else None
        )
        if result.get("status") != "SUCCESS" or exit_code != 0:
            message = str(
                result.get("error")
                or result.get("response")
                or stderr.strip()
                or f"exit {exit_code}"
            )
            if _LIMIT.search(message):
                return RunReport(
                    session, None, limit=UsageLimit(message, self._reset_at(message)), usage=usage
                )
            return RunReport(session, None, usage=usage, error=message)

        outcome, problem = read_outcome(run.worktree)
        return RunReport(session, outcome, usage=usage, error=problem)

    def _reset_at(self, message: str) -> datetime | None:
        """Decode the h/m/s countdown captured in ``fixtures/antigravity/usage-limit.ndjson``."""
        match = _RESET.search(message)
        if match is None or not any(match.groups()):
            return None
        try:
            delay = timedelta(
                **{unit: int(value or 0) for unit, value in match.groupdict().items()}
            )
            return self._now() + delay
        except (ValueError, OverflowError):
            return None


def _events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
