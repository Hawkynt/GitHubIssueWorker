"""What the UI shows and what it can do, independent of where the data comes from.

The live manager and the demo scenarios both implement `Backend`, so the pages render the same way
for both and screenshots show the real UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from github_issue_worker.application.history import RunRecord
from github_issue_worker.configuration import Configuration

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


@dataclass(frozen=True, slots=True)
class Snapshot:
    manager_id: str
    now: datetime
    workers: tuple[WorkerView, ...]
    repositories: tuple[RepositoryView, ...]
    claims: tuple[ClaimView, ...]
    issues: tuple[IssueView, ...] = ()


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
