"""What the UI shows and what it can do, independent of where the data comes from.

The live manager and the demo scenarios both implement `Backend`, so the pages render the same way
for both and screenshots show the real UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from github_issue_worker.application.accounts import AccountUsage
from github_issue_worker.application.history import RunRecord
from github_issue_worker.configuration import Configuration
from github_issue_worker.domain.claim import ClaimChange
from github_issue_worker.domain.outcome import Outcome

STEPS = ("understand", "implement", "verify", "push", "checks", "merge")
"""The steps of an issue run, in order: the lane's track."""

WorkerState = Literal["working", "idle", "paused", "limited", "unavailable"]


@dataclass(frozen=True, slots=True)
class WorkerView:
    name: str
    agent: str
    account: str
    model: str
    """Shown as is: already normalised."""
    state: WorkerState
    reason: str = ""
    limited_until: datetime | None = None
    repository: str | None = None
    issue: int | None = None
    issue_title: str | None = None
    step: str | None = None
    branch: str | None = None
    worktree: str | None = None
    since: datetime | None = None
    merge_conflict: bool = False
    group: str = ""
    """The configured worker this is an instance of (``claude`` for ``claude.2``)."""
    blocker: str = ""
    """What its run waits on that only a person can do; empty when nothing."""
    blocker_url: str = ""
    agent_kind: str = ""
    """The configured kind of this worker — ``claude``, ``codex``, … — which says whose colour its
    lane wears while no model is known yet (F14.19)."""

    @property
    def worker(self) -> str:
        return self.group or self.name


@dataclass(frozen=True, slots=True)
class RepositoryView:
    name: str
    watching_since: datetime | None
    counts: dict[str, int] = field(default_factory=dict)
    """Issues per worker:* state, from the live claims."""


@dataclass(frozen=True, slots=True)
class ClaimView:
    repository: str
    issue: int
    holder: str
    mine: bool
    state: str
    step: str
    renewed: datetime
    expires: datetime
    branch: str
    paused_until: datetime | None = None
    merge_conflict: bool = False
    wait_reason: str = ""
    model: str = ""
    """The model its holder runs, as the holder's manager wrote it into the claim."""


@dataclass(frozen=True, slots=True)
class IssueView:
    """An open issue of a watched repository and whoever works it."""

    repository: str
    number: int
    title: str
    state: str
    """What is happening, in words: "worked here", "waiting for your answer", ..."""
    worker: str | None = None
    manager: str | None = None
    local: bool = False
    """Worked by a worker of this manager, which the UI can pause, stop and show the log of."""
    model: str | None = None
    step: str | None = None
    since: datetime | None = None
    body: str = ""
    """The issue's description, shown as the tooltip of its title."""
    merge_conflict: bool = False
    wait_reason: str = ""
    paused_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    manager_id: str
    now: datetime
    workers: tuple[WorkerView, ...]
    repositories: tuple[RepositoryView, ...]
    claims: tuple[ClaimView, ...]
    issues: tuple[IssueView, ...] = ()
    accounts: tuple[AccountUsage, ...] = ()
    """Each account's usage state, its reset and the issues waiting on it (F13.7)."""


LOG_TAIL = 200
"""Lines of a local agent's output the Issue page shows; the Log page has all of them."""


@dataclass(frozen=True, slots=True)
class IssueDetail:
    """Everything the Issue page knows about one issue (F14.6)."""

    issue: IssueView
    claim: ClaimView | None = None
    """The issue's claim as it is now, held or not; None when it never had one."""
    runs: tuple[RunRecord, ...] = ()
    """Every finished run on it, from its claim and this manager's history, oldest first."""
    history: tuple[ClaimChange, ...] = ()
    """The claim's versions, newest first, renewals folded."""
    journal: str | None = None
    """``.ghiw/journal.md`` of this machine's worktree; None when there is none here."""
    outcome: Outcome | None = None
    """``.ghiw/outcome.json`` of this machine's worktree; None when missing or malformed."""
    log: str | None = None
    """The last `LOG_TAIL` lines of the output of the local run on it; None without one."""
    log_worker: str | None = None
    """The local worker running it, whose full log the Log page shows; a release stops it."""

    @property
    def held(self) -> bool:
        return self.claim is not None and self.claim.state in ("working", "paused")


def tail(text: str, lines: int = LOG_TAIL) -> str:
    """The last ``lines`` lines of ``text``."""
    return "\n".join(text.splitlines()[-lines:])


class ActionError(Exception):
    """An action the UI asked for was refused; the message says why and what to do."""


class Backend(Protocol):
    def snapshot(self) -> Snapshot: ...

    def config(self) -> Configuration: ...

    def save_config(self, config: Configuration) -> None:
        """Persist an already validated configuration atomically and apply it live (F0.7)."""
        ...

    def check_repository(self, repository: str) -> None:
        """Raise `ActionError` unless the account can push to ``repository`` (F1.2)."""
        ...

    def pushable_repositories(self) -> tuple[str, ...]:
        """The repositories the account can push to, for the add box (F14.16). Never waits on
        GitHub: what is known so far, possibly nothing."""
        ...

    def detect_workers(self) -> list[dict[str, object]]: ...

    def set_worker_enabled(self, name: str, enabled: bool) -> None: ...

    def approve_ci(self, name: str) -> None:
        """Approve the CI runs the worker's pull request waits for.

        Raises `ActionError` when nothing waits for an approval.
        """
        ...

    def stop_worker(self, name: str) -> None:
        """Kill the worker's agent, release its issue and pause it (F14.4).

        Raises `ActionError` when the worker is not working on an issue.
        """
        ...

    def open_worktree(self, name: str) -> None:
        """Show the worktree of the worker's issue in the file manager of this machine.

        Raises `ActionError` when the worker is not working on an issue.
        """
        ...

    def history(self) -> list[RunRecord]:
        """Every finished run this manager remembers, oldest first, for the cockpit."""
        ...

    def log(self, name: str) -> str | None:
        """The output of the worker's current or last agent run, or None if it never ran."""
        ...

    def release(self, repository: str, issue: int, force: bool) -> None: ...

    def issue(self, repository: str, number: int) -> IssueDetail | None:
        """What the Issue page shows, or None when the repository is not watched or the issue
        is neither open nor claimed (F14.6)."""
        ...

    def pull_request(self, repository: str, number: int) -> str | None:
        """The URL of the open pull request of the issue's branch, or None (F14.6)."""
        ...

    def skip(self, repository: str, number: int) -> None:
        """Label the issue ``worker:skip``: a run in progress stops at its next checkpoint (F2.7).

        Raises `ActionError` when GitHub refuses.
        """
        ...
