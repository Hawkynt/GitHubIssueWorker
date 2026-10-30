"""The Codex CLI as an agent (F12.4, F13.1).

Verified against codex-cli 0.166.0 (fixtures under ``tests/fixtures/codex``):

- ``codex exec --json`` emits NDJSON: ``thread.started`` carries the session id, ``turn.completed``
  the token usage, ``error``/``turn.failed`` a message.
- ``-o <file>`` writes the final message, which ``--output-schema`` forces into the outcome
  contract, so the outcome file is Codex's own answer.
- With stdin left open Codex waits for more input; the prompt therefore always goes on stdin with
  ``-`` as the prompt argument, which also lifts argv length limits.
- ``codex exec resume <id>`` takes the same flags.
- A usage limit is reported only as text ("You've hit your usage limit ... try again at Mar 20th,
  2027 3:36 PM."), with the reset in the local time zone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.ports.agents import (
    AgentRun,
    Invocation,
    RunReport,
    TokenUsage,
    UsageLimit,
)

_LIMIT = re.compile(r"(hit|reached) (your |the )?usage limit|usage limit reset", re.IGNORECASE)
_RESET = re.compile(r"try again at (?P<when>.+?)\.?\s*$", re.IGNORECASE)
_ORDINAL = re.compile(r"(\d+)(st|nd|rd|th)\b")
_MODEL = re.compile(r'"model"\s*:\s*"([^"]+)"')
_DATE_FORMATS = ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p")
_TIME_FORMATS = ("%I:%M %p", "%H:%M")


class CodexAdapter:
    kind = "codex"

    def __init__(
        self,
        executable: str = "codex",
        timezone: tzinfo | None = None,
        now: Callable[[], datetime] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._executable = executable
        self._env = env or {}
        self._now = now or (lambda: datetime.now(timezone).astimezone())
        self._timezone = timezone

    def invocation(self, run: AgentRun) -> Invocation:
        outcome, schema = prepare(run)
        argv = [self._executable, "exec"]
        if run.session:
            argv += ["resume", run.session]
        argv += [
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema),
            "-o",
            str(outcome),
        ]
        if run.model:
            argv += ["-m", run.model]
        argv.append("-")
        return Invocation(argv=argv, cwd=run.worktree, stdin=run.prompt, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        report = self._report(run, exit_code, stdout, stderr)
        return replace(report, model=self._session_model(report.session))

    def _session_model(self, session: str | None) -> str | None:
        """``codex exec --json`` does not say which model ran; Codex's own session file does."""
        if not session:
            return None
        home = Path(self._env.get("CODEX_HOME") or Path.home() / ".codex")
        for path in sorted((home / "sessions").rglob(f"*{session}*.jsonl"), reverse=True):
            match = _MODEL.search(path.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(1)
        return None

    def _report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        session: str | None = None
        usage: TokenUsage | None = None
        failures: list[str] = []
        for event in _events(stdout):
            kind = event.get("type")
            if kind == "thread.started":
                session = event.get("thread_id") or session
            elif kind == "turn.completed":
                tokens = event.get("usage") or {}
                previous = usage or TokenUsage(0, 0)
                usage = TokenUsage(
                    input_tokens=previous.input_tokens + int(tokens.get("input_tokens", 0)),
                    output_tokens=previous.output_tokens + int(tokens.get("output_tokens", 0)),
                )
            elif kind == "error":
                failures.append(str(event.get("message", "")))
            elif kind == "turn.failed":
                failures.append(str((event.get("error") or {}).get("message", "")))

        for message in failures:
            if _LIMIT.search(message):
                limit = UsageLimit(message=message, reset_at=self._reset_at(message))
                return RunReport(session=session, outcome=None, limit=limit, usage=usage)

        outcome, problem = read_outcome(run.worktree)
        if exit_code != 0 or failures:
            reason = "; ".join(m for m in failures if m) or stderr.strip() or f"exit {exit_code}"
            return RunReport(session=session, outcome=outcome, usage=usage, error=reason)
        return RunReport(session=session, outcome=outcome, usage=usage, error=problem)

    def _reset_at(self, message: str) -> datetime | None:
        match = _RESET.search(message)
        if not match:
            return None
        when = _ORDINAL.sub(r"\1", match["when"]).strip()
        now = self._now()
        zone = self._timezone or now.tzinfo
        for pattern in _DATE_FORMATS:
            try:
                return datetime.strptime(when, pattern).replace(tzinfo=zone)
            except ValueError:
                pass
        for pattern in _TIME_FORMATS:
            try:
                clock = datetime.strptime(when, pattern).time()
            except ValueError:
                continue
            candidate = datetime.combine(now.date(), clock, tzinfo=zone)
            return candidate if candidate > now else candidate + timedelta(days=1)
        return None


def _events(stdout: str) -> list[dict[str, Any]]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
