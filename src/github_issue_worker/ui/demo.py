"""A fixed, believable fleet for ``ui --demo`` and the generated screenshots (F14.7).

The pages are the real ones; only the data is invented: fixed names, times and issues, no network,
no personal data.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from github_issue_worker.application.history import RunRecord
from github_issue_worker.configuration import Configuration, validate
from github_issue_worker.ui.model import (
    ActionError,
    ClaimView,
    IssueView,
    RepositoryView,
    Snapshot,
    WorkerView,
)

NOW = datetime(2026, 10, 30, 14, 20, tzinfo=UTC)
MANAGER = "office-pc-7f3a"

CONFIG = {
    "manager": {"name": "office-pc", "tags": ["linux", "gpu"]},
    "pollInterval": "00:01:00",
    "trustedUsers": ["release-bot-owner"],
    "accounts": {
        "anthropic-max": {"maxConcurrentRuns": 3},
        "openai-plus": {"maxConcurrentRuns": 2},
        "google-pro": {"maxConcurrentRuns": 2},
        "local-gpu": {"maxConcurrentRuns": 1},
    },
    "workers": [
        {
            "name": "claude",
            "agent": "claude",
            "account": "anthropic-max",
            "priority": 1,
            "model": "opus",
            "maxBudgetUsdPerRun": 10,
            "installation": {"type": "claudecodeportable", "profile": "default"},
        },
        {
            "name": "codex",
            "agent": "codex",
            "account": "openai-plus",
            "priority": 2,
            "installation": {"type": "claudecodeportable", "profile": "ChatGPTCodex"},
        },
        {
            "name": "antigravity",
            "agent": "antigravity",
            "account": "google-pro",
            "priority": 3,
            "installation": {"type": "claudecodeportable", "profile": "GoogleGemini"},
        },
        {
            "name": "local",
            "agent": "claude",
            "account": "local-gpu",
            "priority": 9,
            "installation": {"type": "claudelocal"},
        },
        {"name": "opencode", "agent": "opencode", "account": "local-gpu", "priority": 10},
    ],
    "fleet": {"claimValidity": "7.00:00:00", "maxTakeovers": 3},
    "defaults": {"mergeMethod": "squash", "checksTimeout": "01:00:00"},
    "repositories": [
        {"repository": "acme/telemetry-gateway", "test": "pytest -q"},
        {"repository": "acme/billing-cli", "mode": "labeled"},
        {"repository": "acme/design-tokens"},
    ],
}

WORKERS = (
    WorkerView(
        "claude",
        "Claude Code",
        "anthropic-max",
        "Claude Opus 5.5",
        "working",
        repository="acme/telemetry-gateway",
        issue=214,
        issue_title="Retry uploads after a gateway timeout",
        step="verify",
        branch="issue/214-retry-uploads-after-a-gateway-timeout",
        worktree="~/.cache/GitHubIssueWorker/acme/telemetry-gateway/issues/214",
        since=NOW - timedelta(minutes=23),
        group="claude",
    ),
    WorkerView(
        "claude.2",
        "Claude Code",
        "anthropic-max",
        "Claude Opus 5.5",
        "working",
        repository="acme/design-tokens",
        issue=57,
        issue_title="Export tokens as CSS custom properties",
        step="implement",
        branch="issue/57-export-tokens-as-css-custom-properties",
        worktree="~/.cache/GitHubIssueWorker/acme/design-tokens/issues/57",
        since=NOW - timedelta(minutes=9),
        group="claude",
    ),
    WorkerView(
        "claude.3", "Claude Code", "anthropic-max", "Claude Opus 5.5", "idle", group="claude"
    ),
    WorkerView(
        "codex",
        "Codex",
        "openai-plus",
        "GPT 6.1 Sol",
        "working",
        repository="acme/billing-cli",
        issue=88,
        issue_title="Invoices round VAT per line instead of per total",
        step="checks",
        branch="issue/88-invoices-round-vat-per-line-instead-of-per",
        worktree="~/.cache/GitHubIssueWorker/acme/billing-cli/issues/88",
        since=NOW - timedelta(hours=1, minutes=4),
        group="codex",
    ),
    WorkerView("codex.2", "Codex", "openai-plus", "GPT 6.1 Sol", "idle", group="codex"),
    *(
        WorkerView(
            name,
            "Antigravity",
            "google-pro",
            "Gemini 4 Argon (Medium)",
            "limited",
            limited_until=NOW + timedelta(hours=2, minutes=40),
            group="antigravity",
        )
        for name in ("antigravity", "antigravity.2")
    ),
    WorkerView("local", "ClaudeLocal", "local-gpu", "Qwen3.8 Flash Next IQ3_S", "idle"),
    WorkerView(
        "opencode",
        "OpenCode",
        "local-gpu",
        "-",
        "unavailable",
        reason="opencode is not on PATH",
    ),
)

CLAIMS = (
    ClaimView(
        "acme/telemetry-gateway",
        214,
        f"{MANAGER}/claude",
        True,
        "working",
        "implement",
        NOW - timedelta(minutes=4),
        NOW + timedelta(days=7) - timedelta(minutes=4),
        "issue/214-retry-uploads-after-a-gateway-timeout",
    ),
    ClaimView(
        "acme/telemetry-gateway",
        219,
        "laptop-01ab/claude",
        False,
        "paused",
        "implement",
        NOW - timedelta(hours=5),
        NOW + timedelta(days=6, hours=19),
        "issue/219-buffer-metrics-while-offline",
        paused_until=NOW + timedelta(hours=1, minutes=10),
    ),
    ClaimView(
        "acme/design-tokens",
        57,
        f"{MANAGER}/claude.2",
        True,
        "working",
        "implement",
        NOW - timedelta(minutes=1),
        NOW + timedelta(days=7) - timedelta(minutes=1),
        "issue/57-export-tokens-as-css-custom-properties",
    ),
    ClaimView(
        "acme/billing-cli",
        88,
        f"{MANAGER}/codex",
        True,
        "working",
        "push",
        NOW - timedelta(minutes=2),
        NOW + timedelta(days=7) - timedelta(minutes=2),
        "issue/88-invoices-round-vat-per-line-instead-of-per",
    ),
    ClaimView(
        "acme/billing-cli",
        91,
        "ci-runner-03/local",
        False,
        "working",
        "understand",
        NOW - timedelta(days=2, hours=3),
        NOW + timedelta(days=4, hours=21),
        "issue/91-export-statements-as-csv",
    ),
)

REPOSITORIES = (
    RepositoryView(
        "acme/telemetry-gateway",
        NOW - timedelta(days=12),
        {"working": 1, "paused": 1, "needs-input": 2},
    ),
    RepositoryView(
        "acme/billing-cli", NOW - timedelta(days=3), {"working": 2, "awaiting-review": 1}
    ),
    RepositoryView("acme/design-tokens", NOW - timedelta(hours=6), {}),
)


ISSUES: tuple[IssueView, ...] = (
    IssueView(
        "acme/telemetry-gateway",
        214,
        "Retry uploads after a gateway timeout",
        "worked here",
        "claude",
        MANAGER,
        True,
        "Claude Opus 5.5",
        "verify",
        NOW - timedelta(minutes=23),
    ),
    IssueView(
        "acme/telemetry-gateway",
        219,
        "Buffer metrics while offline",
        "paused until the usage limit resets",
        "claude",
        "laptop-01ab",
        False,
        None,
        "implement",
        NOW - timedelta(hours=5),
    ),
    IssueView(
        "acme/telemetry-gateway",
        221,
        "Gateway URL should come from the environment",
        "waiting for your answer",
    ),
    IssueView(
        "acme/billing-cli",
        88,
        "Invoices round VAT per line instead of per total",
        "worked here",
        "codex",
        MANAGER,
        True,
        "GPT 6.1 Sol",
        "checks",
        NOW - timedelta(hours=1, minutes=4),
    ),
    IssueView(
        "acme/billing-cli",
        91,
        "Export statements as CSV",
        "worked elsewhere",
        "local",
        "ci-runner-03",
        False,
        None,
        "understand",
        NOW - timedelta(days=2, hours=3),
    ),
    IssueView(
        "acme/billing-cli", 95, "Show the currency next to every amount", "waiting for review or CI"
    ),
    IssueView(
        "acme/design-tokens", 12, "Rename the colour tokens to their roles", "waiting for a worker"
    ),
)

ISSUES += (
    IssueView(
        "acme/design-tokens",
        57,
        "Export tokens as CSS custom properties",
        "worked here",
        "claude.2",
        MANAGER,
        True,
        "Claude Opus 5.5",
        "implement",
        NOW - timedelta(minutes=9),
    ),
)
BODIES = {
    ("acme/design-tokens", 57): "Apps that do not use the JavaScript package need the tokens too. "
    "Generate a tokens.css with one custom property per token.",
    ("acme/telemetry-gateway", 214): "Uploads to the gateway fail for good after a single 504. "
    "Retry them with a backoff, at most five times, before reporting the batch as lost.",
    ("acme/telemetry-gateway", 219): "When the uplink drops, metrics are discarded. Keep them in "
    "a bounded on-disk buffer and send them once the connection is back.",
    ("acme/billing-cli", 88): "Invoices round the VAT once on the total, which differs by a cent "
    "from what the tax office expects. Round per line, then sum.",
    ("acme/billing-cli", 91): "Statements can only be printed. Add an export as CSV with one row "
    "per booking.",
}
ISSUES = tuple(
    replace(issue, body=BODIES.get((issue.repository, issue.number), "")) for issue in ISSUES
)

LOG = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "thread.started", "thread_id": "demo"},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "The restatement asks for retries with backoff on gateway timeouts; "
                "I start with the failing scenario.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "pytest -q tests/test_uploads.py -k timeout",
                "aggregated_output": "F\n1 failed in 0.21s: test_a_timeout_is_retried_with_backoff",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "sed -n 40,80p src/telemetry/uploads.py",
                "aggregated_output": "def upload(batch):\n    return client.post(batch)",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "Retries now back off 1, 2 and 4 seconds and give up after the configured "
                "count; the scenario passes.",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "128 passed in 3.40s",
            },
        },
    )
)


def demo_history() -> list[RunRecord]:
    """120 days of a believable fleet: models arriving and leaving, a few questions and failures."""
    rng = random.Random(20261030)
    eras = (
        ("Claude Opus 5", "claude", 0, 45, 38),
        ("Claude Opus 5.5", "claude", 40, 120, 31),
        ("GPT 6.1 Sol", "codex", 25, 120, 44),
        ("Gemini 4 Argon (Medium)", "antigravity", 60, 120, 52),
        ("Qwen3.8 Flash Next IQ3_S", "local", 80, 120, 96),
    )
    repositories = ("acme/telemetry-gateway", "acme/billing-cli", "acme/design-tokens")
    start = NOW - timedelta(days=120)
    records: list[RunRecord] = []
    issue = 100
    for day in range(120):
        for model, agent, first, last, minutes in eras:
            if not first <= day < last:
                continue
            for _ in range(rng.choice((0, 0, 1, 1, 1, 2, 2, 3))):
                issue += 1
                began = start + timedelta(days=day, hours=rng.uniform(7, 20))
                took = timedelta(minutes=max(4.0, rng.gauss(minutes, minutes / 3)))
                verdict = rng.choices(
                    ("merged", "needs-input", "failed", "awaiting-review"), (86, 8, 3, 3)
                )[0]
                records.append(
                    RunRecord(
                        repository=rng.choice(repositories),
                        issue=issue,
                        worker=agent,
                        agent=agent,
                        model=model,
                        kind="first",
                        verdict=verdict,
                        started=began,
                        finished=min(began + took, NOW - timedelta(minutes=1)),
                        issue_started=began,
                    )
                )
    return sorted(records, key=lambda record: record.finished)


class DemoBackend:
    """The demo fleet. Changes are kept in memory for the session, so the UI can be tried out."""

    def __init__(self) -> None:
        self._config = validate(json.loads(json.dumps(CONFIG)))
        self._workers = list(WORKERS)
        self.stopped: list[str] = []
        self.opened: list[str] = []
        self._history = demo_history()

    def snapshot(self) -> Snapshot:
        names = {worker.name for worker in self._config.workers}
        workers = tuple(w for w in self._workers if w.worker in names)
        repos = {r.repository for r in self._config.repositories}
        return Snapshot(
            manager_id=MANAGER,
            now=NOW,
            workers=workers,
            repositories=tuple(r for r in REPOSITORIES if r.name in repos)
            + tuple(
                RepositoryView(name, None)
                for name in sorted(repos - {r.name for r in REPOSITORIES})
            ),
            claims=CLAIMS,
            issues=ISSUES,
        )

    def config(self) -> Configuration:
        return self._config

    def save_config(self, config: Configuration) -> None:
        self._config = config

    def check_repository(self, repository: str) -> None:
        if repository.startswith("someone-else/"):
            raise ActionError(
                f"{repository}: your account cannot push there, so it cannot be watched"
            )

    def detect_workers(self) -> list[dict[str, object]]:
        return [
            {
                "name": "codex",
                "agent": "codex",
                "account": "openai-plus",
                "priority": 2,
                "installation": {"type": "claudecodeportable", "profile": "ChatGPTCodex"},
            },
            {
                "name": "local",
                "agent": "claude",
                "account": "local-gpu",
                "priority": 9,
                "installation": {"type": "claudelocal"},
            },
        ]

    def set_worker_enabled(self, name: str, enabled: bool) -> None:
        self._workers = [
            replace(w, state="paused" if not enabled else "idle")
            if name in (w.name, w.worker)
            else w
            for w in self._workers
        ]

    def release(self, repository: str, issue: int, force: bool) -> None:
        return None

    def stop_worker(self, name: str) -> None:
        worker = next((w for w in self._workers if w.name == name), None)
        if worker is None or worker.state != "working":
            raise ActionError(f"{name} is not working on an issue, so there is nothing to stop")
        self.stopped.append(name)
        self.set_worker_enabled(name, False)

    def open_worktree(self, name: str) -> None:
        worker = next((w for w in self._workers if w.name == name), None)
        if worker is None or worker.worktree is None:
            raise ActionError(f"{name} is not working on an issue, so it has no worktree to open")
        self.opened.append(name)

    def history(self) -> list[RunRecord]:
        return self._history

    def log(self, name: str) -> str | None:
        worker = next((w for w in self._workers if w.name == name), None)
        return LOG if worker is not None and worker.state == "working" else None
