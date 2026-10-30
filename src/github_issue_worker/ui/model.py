"""What the UI shows and what it can do, independent of where the data comes from.

The live manager and the demo scenarios both implement `Backend`, so the pages render the same way
for both and screenshots show the real UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

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
class Snapshot:
    manager_id: str
    now: datetime
    workers: tuple[WorkerView, ...]
    repositories: tuple[RepositoryView, ...]
    claims: tuple[ClaimView, ...]


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

    def detect_workers(self) -> list[dict[str, object]]: ...

    def set_worker_enabled(self, name: str, enabled: bool) -> None: ...

    def release(self, repository: str, issue: int, force: bool) -> None: ...
