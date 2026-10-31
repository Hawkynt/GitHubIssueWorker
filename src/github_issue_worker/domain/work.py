"""Whether an issue needs a run now, and which kind (F2.2, F2.7, F8.2, F9.1, F9.2, F11.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState
from github_issue_worker.domain.issue import Actor, Comment, Issue

SKIP_LABEL = "worker:skip"
OPT_IN_LABEL = "worker"
WAITING_STEPS = frozenset({"needs-input", "awaiting-review", "failed", "split"})
"""Steps of a released claim that wait for a person rather than for a worker."""


class RunKind(StrEnum):
    FIRST = "first"
    REWORK = "rework"
    REOPEN = "reopen"
    CONTINUATION = "continuation"


@dataclass(frozen=True, slots=True)
class WorkContext:
    """Everything the decision depends on, already fetched."""

    issue: Issue
    claim: Claim | None
    comments: tuple[Comment, ...]
    trusted: Callable[[Actor], bool]
    mode: Literal["all", "labeled"]
    watched_since: datetime
    policy: ClaimPolicy
    now: datetime


def new_input(context: WorkContext) -> tuple[Comment, ...]:
    """Trusted comments that no completed run has consumed yet, excluding managers' own output."""
    consumed = context.claim.consumed_through.comment if context.claim else None
    return tuple(
        comment
        for comment in context.comments
        if not comment.is_own_output
        and context.trusted(comment.author)
        and (consumed is None or comment.id > consumed)
        and (context.claim is not None or comment.created_at >= context.watched_since)
    )


def pending_run(context: WorkContext) -> RunKind | None:
    """The run the issue needs now, or None when it needs nothing or is somebody else's."""
    issue, claim = context.issue, context.claim
    if issue.is_pull_request or issue.state != "open" or SKIP_LABEL in issue.labels:
        return None
    if context.mode == "labeled" and OPT_IN_LABEL not in issue.labels:
        return None
    if not context.trusted(issue.author):
        return None

    if claim is None:
        if issue.created_at >= context.watched_since or new_input(context):
            return RunKind.FIRST
        return None

    if claim.state.holds:
        return RunKind.CONTINUATION if context.policy.is_abandoned(claim, context.now) else None
    if claim.state is ClaimState.DONE:
        return RunKind.REOPEN
    if claim.step in WAITING_STEPS:
        return RunKind.REWORK if new_input(context) else None
    return RunKind.CONTINUATION
