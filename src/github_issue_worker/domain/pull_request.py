"""Whether an open pull request the manager did not open is worked, and what it asks for (F16).

Pure decisions on facts gathered elsewhere: the poller and the host collect them, the manager acts
on the answer. A pull request is *worked*, *ignored* (silently: not ours to touch) or *unserved*
(it would be worked, but its branch cannot be pushed to, which its author is told once).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SKIP_LABEL = "worker:skip"
OPT_IN_LABEL = "worker"

FAILED_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}
)
"""Check conclusions that need a repair; ``success``, ``neutral`` and ``skipped`` are fine."""

Eligibility = Literal["work", "ignore", "unserved"]
Work = Literal[
    "checks-failed", "changes-requested", "review-threads", "commented", "ready-to-merge"
]


@dataclass(frozen=True, slots=True)
class PullRequestFacts:
    number: int
    state: str
    draft: bool
    labels: frozenset[str]
    same_repository: bool
    """Its branch lives in the watched repository itself, not in a fork."""
    maintainer_can_modify: bool
    """A fork's author allows maintainers to push to the branch."""
    opened_by_manager: bool
    """The manager opened it for an issue: it is worked through that issue, never here."""


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    decision: str
    """GitHub's review decision: ``APPROVED``, ``CHANGES_REQUESTED``, ``REVIEW_REQUIRED``, or
    empty when the repository requires no review."""
    unresolved_threads: int


def eligibility(
    facts: PullRequestFacts, mode: Literal["all", "labeled"], trusted: bool
) -> tuple[Eligibility, str]:
    """Whether the pull request is worked (F16.3), and if not, why."""
    if facts.state != "open":
        return "ignore", "it is not open"
    if facts.opened_by_manager:
        return "ignore", "the manager opened it for an issue"
    if facts.draft:
        return "ignore", "it is a draft"
    if SKIP_LABEL in facts.labels:
        return "ignore", f"it carries {SKIP_LABEL}"
    if mode == "labeled" and OPT_IN_LABEL not in facts.labels:
        return "ignore", f"it does not carry {OPT_IN_LABEL}"
    if not trusted:
        return "ignore", "its author is not trusted"
    if not facts.same_repository and not facts.maintainer_can_modify:
        return (
            "unserved",
            "its branch is in a fork that does not allow edits by maintainers, so no worker can "
            "push to it; allow edits by maintainers on the pull request to have it worked",
        )
    if not facts.same_repository:
        return (
            "unserved",
            "its branch is in a fork, and workers push only to branches of this repository so "
            "far; push the branch here to have it worked",
        )
    return "work", ""


def wanted_work(
    conclusions: tuple[str | None, ...], review: ReviewFacts, new_input: bool
) -> tuple[Work, ...]:
    """What the pull request asks for (F16.2), most urgent first; empty while checks still run
    and nothing else asks. ``None`` is a check that has not finished."""
    wanted: list[Work] = []
    if any(conclusion in FAILED_CONCLUSIONS for conclusion in conclusions):
        wanted.append("checks-failed")
    if review.decision == "CHANGES_REQUESTED":
        wanted.append("changes-requested")
    if review.unresolved_threads:
        wanted.append("review-threads")
    if new_input:
        wanted.append("commented")
    if not wanted and all(conclusion is not None for conclusion in conclusions):
        wanted.append("ready-to-merge")
    return tuple(wanted)
