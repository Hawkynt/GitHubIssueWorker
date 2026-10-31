"""One watched repository on GitHub, as the issue run needs it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from github_issue_worker.domain.issue import Comment, Issue

WORKER_LABELS = {
    "worker:queued": ("ededed", "Waiting for an idle worker"),
    "worker:working": ("1d76db", "A worker is on it"),
    "worker:paused": ("fbca04", "Paused until the agent's usage limit resets"),
    "worker:needs-input": ("d93f0b", "A question is in the issue"),
    "worker:awaiting-review": ("5319e7", "The pull request waits for a review or required checks"),
    "worker:failed": ("b60205", "The worker gave up; the last comment says why"),
    "worker:split": ("0e8a16", "Split into sub-issues; closed once every one is completed"),
    "worker:unserved": ("c5def5", "No worker can serve this issue's routing labels"),
    "worker:skip": ("000000", "Never worked by the issue worker"),
}

CREATED_LABEL_COLOR = "ededed"
"""The neutral colour a triage-created label gets; the specification fixes no colour for them."""


class RepositorySuspended(Exception):
    """Repository work must wait for push access to be confirmed again (F1.5)."""


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    default_branch: str
    can_push: bool
    merge_methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    head_sha: str
    url: str
    mergeable_state: str
    """GitHub's view: clean, unstable, blocked, behind, dirty, unknown, ..."""
    merged: bool = False
    title: str = ""
    state: str = "open"
    """``open`` or ``closed``; a merged pull request is closed."""


@dataclass(frozen=True, slots=True)
class WaitingRun:
    """A workflow run GitHub holds back until someone with write access approves it."""

    id: int
    name: str
    url: str
    event: str = ""
    """What started it: ``pull_request`` is the run branch protection waits for."""
    own: bool = False
    """Its branch lives in this repository, not a fork: only people who can push started it."""


@dataclass(frozen=True, slots=True)
class Blocker:
    """Why a run cannot go on by itself, and what would unblock it."""

    reason: str
    url: str = ""
    approvals: tuple[int, ...] = ()
    """Workflow runs whose approval would unblock it."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    conclusion: str | None
    summary: str = ""
    annotations: tuple[str, ...] = ()
    """Failure annotations of a failed run, e.g. why GitHub never started the job."""


class RepositoryHost(Protocol):
    repository: str

    def info(self) -> RepositoryInfo: ...
    def role(self, login: str) -> str | None: ...
    def issue(self, number: int) -> Issue: ...
    def comments(self, number: int) -> list[Comment]: ...
    def open_issues(self) -> list[Issue]: ...
    def label_names(self) -> set[str]: ...
    def ensure_worker_labels(self) -> None: ...
    def set_worker_label(self, issue: int, label: str | None) -> None: ...
    def add_labels(self, issue: int, labels: list[str]) -> None: ...
    def create_label(self, name: str) -> None: ...
    def comment(self, issue: int, body: str) -> None: ...
    def find_pull_request(self, branch: str) -> PullRequest | None: ...
    def create_pull_request(self, branch: str, base: str, title: str, body: str) -> PullRequest: ...
    def pull_request(self, number: int) -> PullRequest: ...
    def checks(self, sha: str) -> list[Check]: ...
    def merge(self, number: int, method: str, sha: str, title: str = "", message: str = "") -> str:
        """Merge at the expected head; ``title`` and ``message`` become the squash commit's."""
        ...

    def delete_branch(self, branch: str) -> None: ...
    def branches(self, prefix: str) -> list[str]:
        """Names of the remote branches that start with ``prefix``."""
        ...

    def waiting_for_approval(self, sha: str) -> list[WaitingRun]:
        """Workflow runs for ``sha`` held back until someone approves them."""
        ...

    def approve_run(self, run: int) -> None: ...

    def pull_requests_for(self, branch: str) -> list[PullRequest]:
        """Every pull request, open or closed, whose head is ``branch``."""
        ...

    def close_issue(self, issue: int) -> None: ...
    def create_sub_issue(self, parent: int, title: str, body: str, labels: list[str]) -> int:
        """Open an issue and attach it to ``parent`` as a sub-issue; returns its number."""
        ...

    def sub_issues(self, parent: int) -> list[Issue]: ...
