"""A fixed, believable fleet for ``ui --demo`` and the generated screenshots (F14.7).

The pages are the real ones; only the data is invented: fixed names, times and issues, no network,
no personal data.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from github_issue_worker.application.accounts import account_usage
from github_issue_worker.application.history import RunRecord
from github_issue_worker.configuration import Configuration, validate
from github_issue_worker.domain.claim import (
    Claim,
    ClaimPolicy,
    ClaimState,
    Holder,
    RunEntry,
    claim_history,
)
from github_issue_worker.domain.outcome import Outcome, Question
from github_issue_worker.ui.model import (
    ActionError,
    ClaimView,
    IssueDetail,
    IssueView,
    RepositoryView,
    Snapshot,
    WorkerView,
    tail,
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

GOOGLE_RESET = NOW + timedelta(hours=2, minutes=40)
"""When the demo's google-pro account comes back from its usage limit."""

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
        agent_kind="claude",
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
        agent_kind="claude",
    ),
    WorkerView(
        "claude.3",
        "Claude Code",
        "anthropic-max",
        "Claude Opus 5.5",
        "idle",
        group="claude",
        agent_kind="claude",
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
        merge_conflict=True,
        branch="issue/88-invoices-round-vat-per-line-instead-of-per",
        worktree="~/.cache/GitHubIssueWorker/acme/billing-cli/issues/88",
        since=NOW - timedelta(hours=1, minutes=4),
        group="codex",
        blocker="CI's pull request run waits for approval to run; the merge needs it, whatever else already runs",
        blocker_url="https://github.com/acme/billing-cli/actions/runs/9120391",
        agent_kind="codex",
    ),
    WorkerView(
        "codex.2", "Codex", "openai-plus", "GPT 6.1 Sol", "idle", group="codex", agent_kind="codex"
    ),
    WorkerView(
        "antigravity",
        "Antigravity",
        "google-pro",
        "Gemini 4 Argon (Medium)",
        "limited",
        limited_until=GOOGLE_RESET,
        repository="acme/design-tokens",
        issue=61,
        issue_title="Generate dark-mode variants of every surface token",
        step="implement",
        branch="issue/61-generate-dark-mode-variants-of-every-surface",
        worktree="~/.cache/GitHubIssueWorker/acme/design-tokens/issues/61",
        group="antigravity",
        reason="Token allowance reached",
        agent_kind="antigravity",
    ),
    WorkerView(
        "antigravity.2",
        "Antigravity",
        "google-pro",
        "Gemini 4 Argon (Medium)",
        "limited",
        limited_until=GOOGLE_RESET,
        group="antigravity",
        reason="Token allowance reached",
        agent_kind="antigravity",
    ),
    WorkerView(
        "local",
        "ClaudeLocal",
        "local-gpu",
        "Qwen3.8 Flash Next IQ3_S",
        "idle",
        agent_kind="claude",
    ),
    WorkerView(
        "opencode",
        "OpenCode",
        "local-gpu",
        "-",
        "unavailable",
        reason="opencode is not on PATH",
        agent_kind="opencode",
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
        wait_reason="Rate control",
        model="Claude Sonnet 5.5",
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
        merge_conflict=True,
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
        model="Qwen3.8 Flash Next IQ3_S",
    ),
    ClaimView(
        "acme/design-tokens",
        61,
        f"{MANAGER}/antigravity",
        True,
        "paused",
        "implement",
        NOW - timedelta(minutes=6),
        NOW + timedelta(days=7) - timedelta(minutes=6),
        "issue/61-generate-dark-mode-variants-of-every-surface",
        paused_until=GOOGLE_RESET,
        wait_reason="Token allowance reached",
        model="Gemini 4 Argon (Medium)",
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
    RepositoryView("acme/design-tokens", NOW - timedelta(hours=6), {"paused": 1}),
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
        wait_reason="Rate control",
        paused_until=NOW + timedelta(hours=1, minutes=10),
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
        "blocked: CI's pull request run waits for approval to run; the merge needs it, whatever else already runs",
        "codex",
        MANAGER,
        True,
        "GPT 6.1 Sol",
        "checks",
        NOW - timedelta(hours=1, minutes=4),
        merge_conflict=True,
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
    IssueView(
        "acme/design-tokens",
        61,
        "Generate dark-mode variants of every surface token",
        "paused until the usage limit resets",
        "antigravity",
        MANAGER,
        True,
        "Gemini 4 Argon (Medium)",
        "implement",
        NOW - timedelta(minutes=6),
        wait_reason="Token allowance reached",
        paused_until=GOOGLE_RESET,
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


POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
GATEWAY = "acme/telemetry-gateway"


def _gateway_runs() -> tuple[RunRecord, ...]:
    """The finished runs on #214: one other manager's, two of this one."""
    began = NOW - timedelta(days=1, hours=5, minutes=8)

    def run(
        manager: str, worker: str, agent: str, model: str, kind: str, verdict: str, start: datetime
    ) -> RunRecord:
        took = {"done": 18, "needs-input": 26, "failed": 41}[verdict]
        return RunRecord(
            GATEWAY,
            214,
            worker,
            agent,
            model,
            kind,
            verdict,
            start,
            start + timedelta(minutes=took),
            began,
            manager,
        )

    return (
        run("laptop-01ab", "claude", "claude", "Claude Sonnet 5.5", "understand", "done", began),
        run(
            MANAGER,
            "codex",
            "codex",
            "GPT 6.1 Sol",
            "first",
            "needs-input",
            NOW - timedelta(hours=6, minutes=2),
        ),
        run(
            MANAGER,
            "claude",
            "claude",
            "Claude Opus 5.5",
            "continue",
            "failed",
            NOW - timedelta(hours=1, minutes=24),
        ),
    )


def _gateway_claims() -> list[Claim]:
    """Every version of #214's claim, oldest first, as its claim ref would hold them."""
    runs = _gateway_runs()
    entries = tuple(
        RunEntry(r.manager, r.worker, r.agent, r.model, r.kind, r.verdict, r.started, r.finished)
        for r in runs
    )
    branch = "issue/214-retry-uploads-after-a-gateway-timeout"
    laptop = Holder("laptop-01ab", "claude")
    versions = [POLICY.new_claim(214, laptop, runs[0].started - timedelta(minutes=1), branch)]

    def then(at: datetime, **changes: object) -> None:
        versions.append(POLICY.renewed(versions[-1], at, **changes))

    then(runs[0].started, step="understand")
    for minutes in (5, 10, 15):
        then(runs[0].started + timedelta(minutes=minutes))
    then(runs[0].finished, state=ClaimState.RELEASED, step="awaiting-review")
    versions.append(versions[-1].replace(runs=entries[:1]))
    versions.append(
        POLICY.taken_over(versions[-1], Holder(MANAGER, "codex"), runs[1].started, "GPT 6.1 Sol")
    )
    then(runs[1].started + timedelta(minutes=2), step="implement")
    then(runs[1].finished, state=ClaimState.RELEASED, step="needs-input")
    versions.append(versions[-1].replace(runs=entries[:2]))
    claude = Holder(MANAGER, "claude")
    versions.append(POLICY.taken_over(versions[-1], claude, runs[2].started, "Claude Opus 5.5"))
    then(runs[2].started + timedelta(minutes=1), step="understand")
    then(runs[2].finished)
    versions.append(versions[-1].replace(runs=entries))
    for minutes in range(12):
        then(runs[2].finished + timedelta(minutes=2 + 3 * minutes))
    then(NOW - timedelta(minutes=4), step="implement")
    return versions


JOURNAL = """# Journal: acme/telemetry-gateway#214

## Scenarios
- Given a gateway answering 504, when a batch is uploaded, then it is retried with backoff.
- Given <5 retries> all failing, when the last one fails, then the batch is reported as lost.

## Decisions
- Retries back off 1, 2 and 4 seconds; the count comes from `uploads.retries` (default 5).
- The previous run failed on a flaky clock in the test: the backoff now takes a sleep function.

## Done
- failing scenario for the 504 retry; backoff in `upload()`

## Next
- the lost-batch report, then the full suite
"""

QUESTION = Outcome(
    kind="needs-input",
    summary="The gateway URL can come from the environment alone or from the environment over "
    "the configuration file; the two lead to different code.",
    scenarios=("Given GATEWAY_URL is set, when the service starts, then it uploads to that URL.",),
    questions=(
        Question(
            "Where should the gateway URL come from?",
            (
                "GATEWAY_URL only: simplest, but the configuration file loses its gateway key",
                "GATEWAY_URL over the configuration file: both keep working",
            ),
            "GATEWAY_URL over the configuration file, so existing setups keep working",
        ),
    ),
)
"""The outcome #221's understanding run left in its worktree: it asks before it builds."""


def _claimed(claim: ClaimView) -> Claim:
    """The first version of a demo claim: enough history for the issues that are not #214."""
    manager, _, worker = claim.holder.rpartition("/")
    return POLICY.new_claim(claim.issue, Holder(manager, worker), claim.renewed, claim.branch)


def demo_details() -> dict[tuple[str, int], IssueDetail]:
    """What the Issue page shows for every issue of the demo (F14.6)."""
    claims = {(claim.repository, claim.issue): claim for claim in CLAIMS}
    working = {(w.repository, w.issue): w.name for w in WORKERS if w.state == "working"}
    details = {}
    for issue in ISSUES:
        key = (issue.repository, issue.number)
        worker = working.get(key)
        details[key] = IssueDetail(
            issue=issue,
            claim=claims.get(key),
            log=tail(LOG) if worker else None,
            log_worker=worker,
            history=tuple(claim_history([_claimed(claims[key])])) if key in claims else (),
        )
    gateway = (GATEWAY, 214)
    details[gateway] = replace(
        details[gateway],
        runs=_gateway_runs(),
        history=tuple(claim_history(_gateway_claims())),
        journal=JOURNAL,
    )
    question = (GATEWAY, 221)
    details[question] = replace(details[question], outcome=QUESTION)
    return details


PULL_REQUESTS = {
    (GATEWAY, 214): "https://github.com/acme/telemetry-gateway/pull/230",
    ("acme/billing-cli", 88): "https://github.com/acme/billing-cli/pull/96",
}


PUSHABLE = (
    "acme/telemetry-gateway",
    "acme/billing-cli",
    "acme/design-tokens",
    "acme/ingest-worker",
    "acme/status-page",
    "acme/infra-terraform",
    "acme-labs/edge-cache",
    "acme-labs/prototype-search",
    "hana-ito/dotfiles",
    "hana-ito/notes-sync",
)
"""What the demo account can push to, for the add box's suggestions."""


class DemoBackend:
    """The demo fleet. Changes are kept in memory for the session, so the UI can be tried out."""

    def __init__(self) -> None:
        self._config = validate(json.loads(json.dumps(CONFIG)))
        self._workers = list(WORKERS)
        self.stopped: list[str] = []
        self.opened: list[str] = []
        self.approved: list[str] = []
        self.skipped: list[tuple[str, int]] = []
        self.refuse_skip = ""
        self.released: list[tuple[str, int, bool]] = []
        self.details = demo_details()
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
            accounts=account_usage(
                self._config.accounts,
                {worker.name: worker.account for worker in workers},
                {w.account: w.limited_until for w in workers if w.limited_until}.get,
                (
                    (claim.repository, claim.issue, claim.holder.rpartition("/")[2])
                    for claim in CLAIMS
                    if claim.mine and claim.state == "paused"
                ),
                NOW,
            ),
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

    def pushable_repositories(self) -> tuple[str, ...]:
        return PUSHABLE

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
        self.released.append((repository, issue, force))

    def issue(self, repository: str, number: int) -> IssueDetail | None:
        return self.details.get((repository, number))

    def pull_request(self, repository: str, number: int) -> str | None:
        return PULL_REQUESTS.get((repository, number))

    def skip(self, repository: str, number: int) -> None:
        if self.refuse_skip:
            raise ActionError(self.refuse_skip)
        self.skipped.append((repository, number))

    def approve_ci(self, name: str) -> None:
        worker = next((w for w in self._workers if w.name == name), None)
        if worker is None or not worker.blocker:
            raise ActionError(f"{name} has no CI run waiting for an approval")
        self.approved.append(name)
        self._workers = [replace(w, blocker="") if w.name == name else w for w in self._workers]

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
