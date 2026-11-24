"""F16.2 and F16.3 — whether an open pull request is worked, and what it asks for."""

from __future__ import annotations

from dataclasses import replace

import pytest

from github_issue_worker.domain.pull_request import (
    PullRequestFacts,
    ReviewFacts,
    eligibility,
    wanted_work,
)

READY = PullRequestFacts(
    number=7,
    state="open",
    draft=False,
    labels=frozenset(),
    same_repository=True,
    maintainer_can_modify=False,
    opened_by_manager=False,
)


def test_given_an_open_pull_request_in_the_repository_when_judged_then_it_is_worked() -> None:
    assert eligibility(READY, mode="all", trusted=True) == ("work", "")


def test_given_a_fork_that_allows_maintainer_edits_when_judged_then_it_waits_for_fork_pushes() -> (
    None
):
    # F16.5: pushing to a fork is not built yet, so such a pull request says so instead.
    fork = replace(READY, same_repository=False, maintainer_can_modify=True)
    kind, reason = eligibility(fork, mode="all", trusted=True)
    assert kind == "unserved" and "push the branch here" in reason


@pytest.mark.parametrize(
    ("change", "mode"),
    [
        ({"state": "closed"}, "all"),
        ({"draft": True}, "all"),
        ({"opened_by_manager": True}, "all"),
        ({"labels": frozenset({"worker:skip"})}, "all"),
        ({}, "labeled"),
    ],
)
def test_given_a_pull_request_that_is_not_ours_to_work_when_judged_then_it_is_ignored(
    change: dict[str, object], mode: str
) -> None:
    kind, _ = eligibility(replace(READY, **change), mode=mode, trusted=True)  # type: ignore[arg-type]
    assert kind == "ignore"


def test_given_the_worker_label_in_labeled_mode_when_judged_then_it_is_worked() -> None:
    labeled = replace(READY, labels=frozenset({"worker"}))
    assert eligibility(labeled, mode="labeled", trusted=True) == ("work", "")


def test_given_an_untrusted_author_when_judged_then_it_is_ignored() -> None:
    assert eligibility(READY, mode="all", trusted=False)[0] == "ignore"


def test_given_a_fork_that_forbids_maintainer_edits_when_judged_then_it_is_unserved() -> None:
    fork = replace(READY, same_repository=False, maintainer_can_modify=False)
    kind, reason = eligibility(fork, mode="all", trusted=True)
    assert kind == "unserved"
    assert "allow edits by maintainers" in reason


NO_REVIEW = ReviewFacts(decision="", unresolved_threads=0)


def test_given_green_checks_and_no_review_concerns_when_asked_then_it_is_ready_to_merge() -> None:
    assert wanted_work(("success", "neutral", "skipped"), NO_REVIEW, new_input=False) == (
        "ready-to-merge",
    )


@pytest.mark.parametrize("failed", ["failure", "timed_out", "cancelled", "startup_failure"])
def test_given_a_failed_check_when_asked_then_the_checks_need_repair(failed: str) -> None:
    assert wanted_work(("success", failed), NO_REVIEW, new_input=False) == ("checks-failed",)


def test_given_a_running_check_when_asked_then_nothing_is_wanted_yet() -> None:
    assert wanted_work(("success", None), NO_REVIEW, new_input=False) == ()


def test_given_every_concern_when_asked_then_each_is_named_in_order() -> None:
    review = ReviewFacts(decision="CHANGES_REQUESTED", unresolved_threads=2)
    assert wanted_work(("failure",), review, new_input=True) == (
        "checks-failed",
        "changes-requested",
        "review-threads",
        "commented",
    )


def test_given_requested_changes_when_checks_are_green_then_it_is_not_ready_to_merge() -> None:
    review = ReviewFacts(decision="CHANGES_REQUESTED", unresolved_threads=0)
    assert wanted_work(("success",), review, new_input=False) == ("changes-requested",)


def test_given_unresolved_threads_only_when_asked_then_they_are_the_work() -> None:
    review = ReviewFacts(decision="APPROVED", unresolved_threads=1)
    assert wanted_work(("success",), review, new_input=False) == ("review-threads",)


def test_given_no_checks_at_all_when_asked_then_it_is_ready_to_merge() -> None:
    assert wanted_work((), NO_REVIEW, new_input=False) == ("ready-to-merge",)
