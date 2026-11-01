"""Claude Code as an agent (F12.3), directly or through ClaudeLocal (F15.4).

Verified against Claude Code 2.1.314 (``tests/fixtures/claude``): ``-p --output-format stream-json
--verbose`` emits a ``system/init`` and a ``result`` carrying ``session_id``; ``--json-schema`` puts
the outcome into ``result.structured_output``; ``total_cost_usd`` is the run's cost; and every run
carries a ``rate_limit_event`` whose ``status`` is ``allowed``, ``allowed_warning`` or ``rejected``,
with ``utilization`` and ``resetsAt`` in epoch seconds. The prompt is read from stdin.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.domain.outcome import OUTCOME_SCHEMA
from github_issue_worker.domain.run_limit import RunCutOff
from github_issue_worker.ports.agents import (
    OUTCOME_FILE,
    AgentRun,
    Invocation,
    RunObservation,
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

_BUDGET_STOP = "error_max_budget_usd"
"""The ``result`` subtype of a run that ``--max-budget-usd`` stopped (captured, 2.1.316)."""
_DOLLARS = re.compile(r"\$\d+(?:\.\d+)?")


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(structured), encoding="utf-8")


def _budget_text(event: dict[str, Any], budget_usd: float | None) -> str:
    """The budget as Claude names it ("Reached maximum budget ($0.001)"), else as configured."""
    for message in event.get("errors") or []:
        found = _DOLLARS.search(str(message))
        if found:
            return found.group()
    return f"${budget_usd:g}" if budget_usd is not None else "the configured budget"


def _interpret_events(
    run: AgentRun,
    events: list[dict[str, Any]],
    exit_code: int,
    stderr: str,
    budget_usd: float | None = None,
) -> RunReport:
    session: str | None = None
    usage: TokenUsage | None = None
    utilization: float | None = None
    rejected_reset: datetime | None = None
    limited = False
    failure: str | None = None
    cut_off: RunCutOff | None = None

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
            if event.get("subtype") == "success" and not event.get("is_error"):
                limited = False
                rejected_reset = None
                failure = None
                cut_off = None
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
            if event.get("subtype") == _BUDGET_STOP:
                cut_off = RunCutOff("budget", _budget_text(event, budget_usd))

    if limited:
        message = "usage limit reached"
        return RunReport(
            session=session,
            outcome=None,
            limit=UsageLimit(message=message, reset_at=rejected_reset),
            usage=usage,
            utilization=utilization,
        )

    if cut_off is not None:
        return RunReport(
            session, None, usage=usage, error=failure, utilization=utilization, cut_off=cut_off
        )

    outcome, problem = read_outcome(run.worktree)
    if exit_code != 0 or failure or not events:
        reason = failure or stderr.strip() or f"exit {exit_code}"
        return RunReport(session, outcome, usage=usage, error=reason, utilization=utilization)
    return RunReport(session, outcome, usage=usage, error=problem, utilization=utilization)


def _interpret(
    run: AgentRun,
    events: list[dict[str, Any]],
    exit_code: int,
    stderr: str,
    budget_usd: float | None = None,
) -> RunReport:
    observed = _observe_events(events)
    report = _interpret_events(run, events, exit_code, stderr, budget_usd)
    return replace(
        report,
        model=observed.models[-1] if observed.models else None,
        models=tuple(dict.fromkeys(observed.models)),
        worked=observed.worked or (report.limit is None and report.outcome is not None),
    )


def _observe_events(events: list[dict[str, Any]]) -> RunObservation:
    selected: str | None = None
    models: list[str] = []
    session: str | None = None
    worked = False
    for event in events:
        session = event.get("session_id") or session
        kind = event.get("type")
        if kind == "system" and isinstance(event.get("model"), str):
            selected = event["model"]
        elif kind == "assistant" and not (
            event.get("isApiErrorMessage") or event.get("api_error") or event.get("error")
        ):
            message = event.get("message") or {}
            model = message.get("model") or selected
            if message.get("content") and model != "<synthetic>":
                worked = True
                if isinstance(model, str) and (not models or models[-1] != model):
                    models.append(model)
        elif kind == "stream_event":
            info = event.get("event") or {}
            message = info.get("message") or {}
            if info.get("type") == "message_start" and isinstance(message.get("model"), str):
                selected = message["model"]
            if info.get("type") in ("content_block_start", "content_block_delta"):
                worked = True
                if selected and (not models or models[-1] != selected):
                    models.append(selected)
    return RunObservation(session, tuple(models or ([selected] if selected else [])), worked)


class ClaudeAdapter:
    kind = "claude"

    def observe(self, run: AgentRun, stdout: str) -> RunObservation:
        return _observe_events(_events(stdout))

    def __init__(
        self, executable: str, env: dict[str, str] | None = None, budget_usd: float | None = None
    ) -> None:
        self._executable = executable
        self._env = env or {}
        self._budget_usd = budget_usd

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
        if self._budget_usd is not None:
            argv += ["--max-budget-usd", str(self._budget_usd)]
        return Invocation(argv=argv, cwd=run.worktree, stdin=run.prompt, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        return _interpret(run, _events(stdout), exit_code, stderr, self._budget_usd)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_LISTED = re.compile(
    r"^\s*(?P<id>\S+)\s+\S+\s+[\d.]+ GB\s+ctx \S+\s+(?:no fit|\S+)\s+(?P<name>.+?)(?: - |\s{2,}|$)"
)


def installed_models(listing: str) -> list[str]:
    """The ids of the models ClaudeLocal's ``--list`` shows as installed."""
    return [
        match["id"]
        for line in _ANSI.sub("", listing).splitlines()
        if (match := _LISTED.match(line)) and " installed " in f" {line} "
    ]


def model_names(listing: str) -> dict[str, str]:
    """Model id to human name, from ClaudeLocal's ``--list`` output."""
    names = {}
    for line in _ANSI.sub("", listing).splitlines():
        match = _LISTED.match(line)
        if match:
            names[match["id"]] = match["name"].strip()
    return names


class ClaudeLocalAdapter:
    """ClaudeLocal's own Claude Code on a local model, through its launcher.

    ``claude.sh --dir <worktree> --prompt -`` continues the newest session of that directory or
    starts one, reads the prompt from stdin and exits; the worktree is per issue, so its session is
    too. It prints one JSON result, and runs without permission prompts by default.
    """

    kind = "claude"

    def __init__(
        self,
        launcher: Path,
        env: dict[str, str] | None = None,
        list_models: Callable[[], str] | None = None,
    ) -> None:
        self._launcher = launcher
        self._env = env or {}
        self._list_models = list_models or self._run_list
        self._names: dict[str, str] | None = None

    def _run_list(self) -> str:
        """``claude.sh --list``: read-only, it prints the installed and available models."""
        try:
            listed = subprocess.run(
                [str(self._launcher), "--list"], capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return listed.stdout

    def _describe(self, model: str) -> str:
        """The gateway reports its model id; the launcher's list has the human name for it."""
        if self._names is None:
            self._names = model_names(self._list_models())
        known = next(
            (key for key in sorted(self._names, key=len, reverse=True) if model.endswith(key)),
            None,
        )
        return self._names[known] if known else model

    def default_model(self) -> str | None:
        """The model the launcher serves without ``--model``: its one installed model."""
        listing = self._list_models()
        if self._names is None:
            self._names = model_names(listing)
        installed = installed_models(listing)
        return self._describe(installed[0]) if len(installed) == 1 else None

    def invocation(self, run: AgentRun) -> Invocation:
        prepare(run)
        argv = [str(self._launcher), "--dir", str(run.worktree), "--prompt", "-"]
        argv += ["--output-format", "json"]
        if run.model:
            argv += ["--model", run.model]
        return Invocation(argv=argv, cwd=run.worktree, stdin=run.prompt, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        report = _interpret(run, _events(stdout), exit_code, stderr)
        return replace(
            report,
            model=self._describe(report.model) if report.model else None,
            models=tuple(self._describe(model) for model in report.models),
        )

    def observe(self, run: AgentRun, stdout: str) -> RunObservation:
        observed = _observe_events(_events(stdout))
        return replace(observed, models=tuple(self._describe(model) for model in observed.models))
