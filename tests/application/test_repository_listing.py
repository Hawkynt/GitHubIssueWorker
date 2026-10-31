from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from github_issue_worker.application.repository_listing import RepositoryListing, render_list
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from github_issue_worker.domain.issue import Actor, Issue

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
POLICY = ClaimPolicy(timedelta(days=7), timedelta(days=1))


def issue(number: int, **changes: Any) -> Issue:
    return replace(
        Issue(number, f"Title {number}", "", "open", Actor("owner"), frozenset(), False, NOW, NOW),
        **changes,
    )


def test_list_shows_current_issues_state_counts_and_local_and_remote_holders() -> None:
    working = POLICY.new_claim(1, Holder("here", "w"), NOW, "issue/1").replace(step="implement")
    paused = POLICY.new_claim(2, Holder("away", "v"), NOW, "issue/2").replace(
        state=ClaimState.PAUSED
    )
    rows = [
        issue(1),
        issue(2),
        issue(3),
        issue(4, labels=frozenset({"worker:needs-input"})),
        issue(5, state="closed"),
        issue(6, is_pull_request=True),
    ]
    output = render_list(
        [RepositoryListing("o/r", NOW, tuple(rows), (working, paused))], POLICY, NOW
    )
    for expected in (
        "o/r",
        "2026-10-31 12:00",
        "working=1",
        "paused=1",
        "queued=1",
        "needs-input=1",
        "#1",
        "Title 1",
        "here/w",
        "away/v",
    ):
        assert expected in output
    assert "#5" not in output and "#6" not in output


def test_list_handles_empty_watch_list_and_never_polled_empty_repository() -> None:
    assert "no repositories watched" in render_list([], POLICY, NOW)
    output = render_list([RepositoryListing("o/empty", None, (), ())], POLICY, NOW)
    assert "never polled" in output and "no current issues" in output and "queued=0" in output


def test_released_and_abandoned_claims_are_queued_not_busy() -> None:
    released = POLICY.new_claim(1, Holder("here", "w"), NOW, "issue/1").replace(
        state=ClaimState.RELEASED
    )
    old = POLICY.new_claim(2, Holder("gone", "w"), NOW - timedelta(days=9), "issue/2")
    rows = tuple(issue(n, labels=frozenset({"worker:working"})) for n in (1, 2))
    output = render_list([RepositoryListing("o/r", None, rows, (released, old))], POLICY, NOW)
    assert "queued=2" in output and "working=0" in output
    assert "here/w" not in output and "gone/w" not in output


@pytest.mark.parametrize(
    ("claim_state", "step", "expected"),
    [
        (ClaimState.WORKING, "understand", "triage"),
        (ClaimState.WORKING, "implement", "working"),
        (ClaimState.WORKING, "checks", "shipping"),
        (ClaimState.PAUSED, "implement", "paused"),
        (ClaimState.RELEASED, "needs-input", "needs-input"),
        (ClaimState.RELEASED, "awaiting-review", "awaiting-review"),
        (ClaimState.RELEASED, "failed", "failed"),
        (ClaimState.RELEASED, "split", "split"),
        (ClaimState.DONE, "done", "queued"),
    ],
)
def test_list_counts_current_issue_lifecycle_instead_of_raw_claim_states(
    claim_state: ClaimState, step: str, expected: str
) -> None:
    claim = POLICY.new_claim(1, Holder("here", "w"), NOW, "issue/1").replace(
        state=claim_state, step=step
    )
    output = render_list([RepositoryListing("o/r", None, (issue(1),), (claim,))], POLICY, NOW)
    assert f"{expected}=1" in output
    assert "released=" not in output and "done=" not in output


@pytest.mark.parametrize(
    "label",
    ["worker:unserved", "worker:awaiting-review", "worker:failed", "worker:split", "worker:skip"],
)
def test_list_counts_unclaimed_waiting_and_skipped_issues(label: str) -> None:
    output = render_list(
        [RepositoryListing("o/r", None, (issue(1, labels=frozenset({label})),), ())], POLICY, NOW
    )
    state = "skipped" if label == "worker:skip" else label.removeprefix("worker:")
    assert f"{state}=1" in output and "#1" in output
