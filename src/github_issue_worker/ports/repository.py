"""One watched repository on GitHub, as the issue run needs it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from github_issue_worker.domain.issue import Issue

WORKER_LABELS = {
    "worker:queued": ("ededed", "Waiting for an idle worker"),
    "worker:working": ("1d76db", "A worker is on it"),
    "worker:paused": ("fbca04", "Paused until the agent's usage limit resets"),
    "worker:needs-input": ("d93f0b", "A question is in the issue"),
    "worker:awaiting-review": ("5319e7", "The pull request waits for a review or required checks"),
    "worker:failed": ("b60205", "The worker gave up; the last comment says why"),
    "worker:unserved": ("c5def5", "No worker can serve this issue's routing labels"),
    "worker:skip": ("000000", "Never worked by the issue worker"),
}


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


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    conclusion: str | None
    summary: str = ""


class RepositoryHost(Protocol):
    repository: str

    def info(self) -> RepositoryInfo: ...
    def role(self, login: str) -> str | None: ...
    def issue(self, number: int) -> Issue: ...
    def label_names(self) -> set[str]: ...
    def ensure_worker_labels(self) -> None: ...
    def set_worker_label(self, issue: int, label: str | None) -> None: ...
    def add_labels(self, issue: int, labels: list[str]) -> None: ...
    def comment(self, issue: int, body: str) -> None: ...
    def find_pull_request(self, branch: str) -> PullRequest | None: ...
    def create_pull_request(self, branch: str, base: str, title: str, body: str) -> PullRequest: ...
    def pull_request(self, number: int) -> PullRequest: ...
    def checks(self, sha: str) -> list[Check]: ...
    def merge(self, number: int, method: str, sha: str) -> str: ...
    def delete_branch(self, branch: str) -> None: ...
    def close_issue(self, issue: int) -> None: ...
