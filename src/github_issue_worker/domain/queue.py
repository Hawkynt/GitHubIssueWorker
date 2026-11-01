"""The order in which a repository's queued issues are taken (F3.1, F3.7)."""

from __future__ import annotations

from datetime import datetime

from github_issue_worker.domain.issue import Issue

HIGH_PRIORITY_LABEL = "priority:high"
LOW_PRIORITY_LABEL = "priority:low"


def queue_rank(labels: frozenset[str]) -> int:
    """0 for ``priority:high``, 1 for no priority label, 2 for ``priority:low``; high wins a tie."""
    names = {label.casefold() for label in labels}
    if HIGH_PRIORITY_LABEL in names:
        return 0
    if LOW_PRIORITY_LABEL in names:
        return 2
    return 1


def queue_key(issue: Issue) -> tuple[int, datetime, int]:
    """Sort key: priority band first, then the oldest pending event, then the issue number."""
    return queue_rank(issue.labels), issue.updated_at, issue.number
