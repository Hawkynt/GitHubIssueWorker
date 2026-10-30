from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.outcome import SubIssue
from github_issue_worker.domain.split import (
    SPLIT_LABEL,
    inherited_labels,
    merged_pull,
    missing_sub_issues,
    parent_of,
    split_finished,
    split_summary,
    sub_issue_body,
)
from github_issue_worker.product import MARKER_PREFIX

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CSV = SubIssue("Export CSV", "Add a CSV export.", ("Given rows When exported Then a CSV file",))


def issue(number: int, body: str = "", state: str = "open", reason: str | None = None) -> Issue:
    return Issue(
        number,
        f"Issue {number}",
        body,
        state,  # type: ignore[arg-type]
        Actor("Hawkynt"),
        frozenset(),
        False,
        T0,
        T0,
        state_reason=reason,
    )


def test_the_split_label_is_a_worker_label() -> None:
    assert SPLIT_LABEL == "worker:split"


def test_a_sub_issue_body_links_the_parent_and_carries_its_scenarios() -> None:
    body = sub_issue_body(33, CSV)
    assert "Part of #33" in body
    assert "Add a CSV export." in body
    assert "- Given rows When exported Then a CSV file" in body


def test_the_parent_is_read_back_from_a_sub_issue_body() -> None:
    assert parent_of(issue(40, sub_issue_body(33, CSV))) == 33


def test_an_issue_that_only_mentions_part_of_is_no_sub_issue() -> None:
    assert parent_of(issue(40, "Part of #33, written by hand.")) is None


def test_routing_and_priority_labels_are_inherited_others_are_not() -> None:
    labels = {"agent:codex", "needs:gpu", "priority:high", "bug", "worker:working", "worker"}
    assert inherited_labels(labels) == ["agent:codex", "needs:gpu", "priority:high"]


def test_existing_sub_issues_are_matched_by_title() -> None:
    json = SubIssue("Export JSON", "Add a JSON export.", ())
    existing = [replace(issue(40), title="Export CSV")]
    assert missing_sub_issues((CSV, json), existing) == (json,)


def test_a_split_is_finished_when_every_sub_issue_is_completed() -> None:
    children = [
        issue(40, state="closed", reason="completed"),
        issue(41, state="closed", reason="completed"),
    ]
    assert split_finished(children)


def test_a_split_is_not_finished_while_a_sub_issue_is_open() -> None:
    assert not split_finished([issue(40, state="closed", reason="completed"), issue(41)])


def test_a_split_is_not_finished_when_a_sub_issue_was_not_planned() -> None:
    children = [
        issue(40, state="closed", reason="completed"),
        issue(41, state="closed", reason="not_planned"),
    ]
    assert not split_finished(children)


def test_a_split_without_sub_issues_is_not_finished() -> None:
    assert not split_finished([])


def test_the_summary_lists_every_sub_issue_and_its_pull_request() -> None:
    children = [
        issue(40, state="closed", reason="completed"),
        issue(41, state="closed", reason="completed"),
    ]
    text = split_summary(children, {40: "https://x/pull/50"})
    assert "#40" in text and "https://x/pull/50" in text
    assert "#41" in text


def test_the_merged_pull_request_is_read_from_the_managers_closing_comment() -> None:
    own = Comment(
        1,
        40,
        Actor("Hawkynt"),
        f"Merged in https://x/pull/50 (m1).\n\n{MARKER_PREFIX}c -->",
        T0,
        T0,
    )
    by_hand = Comment(2, 40, Actor("Hawkynt"), "Merged in https://x/pull/99 by hand", T0, T0)
    assert merged_pull([by_hand, own]) == "https://x/pull/50"
    assert merged_pull([by_hand]) is None
