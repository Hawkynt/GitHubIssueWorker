"""Issues and comments as the manager sees them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from github_issue_worker.product import MARKER_PREFIX


@dataclass(frozen=True, slots=True)
class Actor:
    """A GitHub account."""

    login: str
    is_bot: bool = False


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str
    state: Literal["open", "closed"]
    author: Actor
    labels: frozenset[str]
    is_pull_request: bool
    created_at: datetime
    updated_at: datetime
    state_reason: str | None = None
    """Why a closed issue was closed: ``completed``, ``not_planned``, ... (F4.6)."""


@dataclass(frozen=True, slots=True)
class Comment:
    id: int
    issue: int
    author: Actor
    body: str
    created_at: datetime
    updated_at: datetime

    @property
    def is_own_output(self) -> bool:
        """Written by a manager, on any machine: it carries the hidden marker (F2.3)."""
        return MARKER_PREFIX in self.body
