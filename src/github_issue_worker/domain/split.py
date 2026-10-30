"""Splitting a large issue into sub-issues that are worked on their own (F4.6).

A sub-issue carries a hidden marker naming its parent, so any manager recognises it without asking
GitHub. The parent waits, labelled ``worker:split``, until every sub-issue is closed as completed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from github_issue_worker.domain.issue import Comment, Issue
from github_issue_worker.domain.outcome import SubIssue
from github_issue_worker.product import MARKER_PREFIX

SPLIT_LABEL = "worker:split"
SPLIT_STEP = "split"
"""The step a split parent's claim is released with."""
INHERITED_PREFIXES = ("agent:", "needs:", "priority:")
"""Labels a sub-issue takes over from its parent: routing and priority."""
_PARENT = re.compile(re.escape(MARKER_PREFIX) + r"sub-issue of #(\d+) -->")
_MERGED = re.compile(r"^Merged in (\S+)")
"""The closing comment a manager posts on an issue it merged."""


def sub_issue_body(parent: int, sub: SubIssue) -> str:
    parts = [f"Part of #{parent}.", ""]
    if sub.body:
        parts += [sub.body, ""]
    if sub.scenarios:
        parts += ["## Acceptance scenarios", ""]
        parts += [f"- {scenario}" for scenario in sub.scenarios]
        parts.append("")
    parts.append(f"{MARKER_PREFIX}sub-issue of #{parent} -->")
    return "\n".join(parts)


def parent_of(issue: Issue) -> int | None:
    """The issue this one was split from, or None when it was not created by a split."""
    match = _PARENT.search(issue.body)
    return int(match[1]) if match else None


def inherited_labels(labels: Iterable[str]) -> list[str]:
    return sorted(label for label in labels if label.startswith(INHERITED_PREFIXES))


def missing_sub_issues(
    planned: tuple[SubIssue, ...], existing: Iterable[Issue]
) -> tuple[SubIssue, ...]:
    """The planned sub-issues that do not exist yet, matched by title: a retry makes no twin."""
    titles = {issue.title.strip() for issue in existing}
    return tuple(sub for sub in planned if sub.title not in titles)


def split_finished(children: Iterable[Issue]) -> bool:
    """Every sub-issue is closed as completed. One closed as not planned leaves it to a person."""
    children = list(children)
    return bool(children) and all(
        child.state == "closed" and child.state_reason == "completed" for child in children
    )


def merged_pull(comments: Iterable[Comment]) -> str | None:
    """The pull request a manager merged for an issue, read from its own closing comment."""
    for comment in comments:
        match = _MERGED.match(comment.body)
        if comment.is_own_output and match:
            return match[1]
    return None


def split_summary(children: Iterable[Issue], pulls: Mapping[int, str]) -> str:
    lines = ["Every sub-issue is done:", ""]
    for child in children:
        pull = pulls.get(child.number)
        lines.append(f"- #{child.number} {child.title}" + (f" in {pull}" if pull else ""))
    return "\n".join(lines)
