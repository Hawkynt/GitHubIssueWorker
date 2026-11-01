"""The lifecycle state displayed for an open issue (F1.4)."""

from __future__ import annotations

from datetime import datetime

from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState
from github_issue_worker.domain.issue import Issue

ISSUE_STATES = (
    "queued",
    "unserved",
    "triage",
    "working",
    "paused",
    "shipping",
    "needs-input",
    "awaiting-review",
    "failed",
    "split",
    "skipped",
)


def issue_state(issue: Issue, claim: Claim | None, policy: ClaimPolicy, now: datetime) -> str:
    if "worker:skip" in issue.labels:
        return "skipped"
    if claim is not None:
        if policy.is_abandoned(claim, now):
            return "queued"
        if claim.state is ClaimState.PAUSED:
            return "paused"
        if claim.state is ClaimState.WORKING:
            if claim.step in ("triage", "understand"):
                return "triage"
            if claim.step in ("push", "checks", "merge"):
                return "shipping"
            return "working"
        if claim.state is ClaimState.RELEASED and claim.step in (
            "needs-input",
            "awaiting-review",
            "failed",
            "split",
        ):
            return claim.step
        return "queued"
    for state in ISSUE_STATES:
        if f"worker:{state}" in issue.labels:
            return state
    return "queued"
