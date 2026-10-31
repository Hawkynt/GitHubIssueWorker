"""The issue's working copy, as the issue run needs it (F5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    message: str


class WorkspacePort(Protocol):
    def worktree(
        self, repository: str, issue: int, branch: str, base: str, resume: bool = True
    ) -> Path: ...
    def checkpoint(self, repository: str, issue: int, branch: str, base: str) -> str: ...
    def commits(self, worktree: Path, base: str) -> list[CommitInfo]: ...
    def head(self, worktree: Path) -> str: ...
    def fork_point(self, worktree: Path, base: str) -> str:
        """The base commit the branch started from, which the remote already has."""
        ...

    def rebase(self, worktree: Path, base: str) -> bool: ...
    def push(self, repository: str, worktree: Path, branch: str, expected: str | None) -> str: ...
    def remote_head(self, repository: str, branch: str) -> str | None: ...
    def remove(self, repository: str, issue: int, branch: str) -> None: ...
    def journal(self, worktree: Path) -> str | None:
        """The agent's journal in the worktree (``.ghiw/journal.md``), or None when it has none."""
        ...

    def worktrees(self, repository: str) -> dict[int, str]:
        """The issues that have a worktree on this machine, with the branch it has checked out."""
        ...
