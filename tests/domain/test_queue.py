from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_issue_worker.domain.issue import Actor, Issue
from github_issue_worker.domain.queue import queue_key, queue_rank

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def issue(number: int, *labels: str, minutes: int = 0) -> Issue:
    at = T0 + timedelta(minutes=minutes)
    return Issue(number, "t", "", "open", Actor("Hawkynt"), frozenset(labels), False, at, at)


def ordered(*issues: Issue) -> list[int]:
    return [item.number for item in sorted(issues, key=queue_key)]


def test_given_low_unlabelled_and_high_when_ordered_then_high_unlabelled_low() -> None:
    low = issue(1, "priority:low", minutes=0)
    normal = issue(2, "bug", minutes=5)
    high = issue(3, "priority:high", minutes=10)
    assert ordered(low, normal, high) == [3, 2, 1]


def test_given_equal_priority_when_ordered_then_the_oldest_update_comes_first() -> None:
    for label in ("priority:high", "bug", "priority:low"):
        assert ordered(issue(1, label, minutes=9), issue(2, label, minutes=1)) == [2, 1]


def test_given_equal_update_times_when_ordered_then_the_lower_number_comes_first() -> None:
    assert ordered(issue(7), issue(3)) == [3, 7]


def test_given_both_priority_labels_when_ranked_then_high_wins() -> None:
    assert queue_rank(frozenset({"priority:low", "priority:high"})) == queue_rank(
        frozenset({"priority:high"})
    )


def test_given_a_differently_cased_label_when_ranked_then_it_still_counts() -> None:
    assert queue_rank(frozenset({"Priority:High"})) == queue_rank(frozenset({"priority:high"}))
    assert queue_rank(frozenset({"PRIORITY:LOW"})) == queue_rank(frozenset({"priority:low"}))


def test_given_no_priority_label_when_ranked_then_it_sits_between_high_and_low() -> None:
    high = queue_rank(frozenset({"priority:high"}))
    low = queue_rank(frozenset({"priority:low"}))
    assert high < queue_rank(frozenset({"priority:medium", "bug"})) < low
