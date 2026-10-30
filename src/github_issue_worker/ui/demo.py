"""A fixed, believable fleet for ``ui --demo`` and the generated screenshots (F14.7).

The pages are the real ones; only the data is invented: fixed names, times and issues, no network,
no personal data.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from github_issue_worker.configuration import Configuration, validate
from github_issue_worker.ui.model import (
    ActionError,
    ClaimView,
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
        "anthropic-max": {},
        "openai-plus": {},
        "google-pro": {},
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
    ),
    WorkerView(
        "antigravity",
        "Antigravity",
        "google-pro",
        "Gemini 4 Argon (Medium)",
        "limited",
        limited_until=NOW + timedelta(hours=2, minutes=40),
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


class DemoBackend:
    """The demo fleet. Changes are kept in memory for the session, so the UI can be tried out."""

    def __init__(self) -> None:
        self._config = validate(json.loads(json.dumps(CONFIG)))
        self._workers = list(WORKERS)

    def snapshot(self) -> Snapshot:
        names = {worker.name for worker in self._config.workers}
        workers = tuple(w for w in self._workers if w.name in names)
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
            replace(w, state="paused" if not enabled else "idle") if w.name == name else w
            for w in self._workers
        ]

    def release(self, repository: str, issue: int, force: bool) -> None:
        return None
