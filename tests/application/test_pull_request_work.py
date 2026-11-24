"""F16.3 and F16.4 — which open pull requests the manager claims, and when."""

from __future__ import annotations

from dataclasses import replace

from github_issue_worker.domain.issue import Issue
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.repository import Check, PullRequest, ReviewState, ReviewThread
from tests.application.test_manager import OWNER, T0, Fixture, Recorded, slot

REPO = "Hawkynt/ShiftDiff"


def opened(
    f: Fixture,
    number: int = 7,
    *,
    branch: str = "retry-uploads",
    repository: str = REPO,
    can_modify: bool = False,
    draft: bool = False,
    checks: list[Check] | None = None,
    review: ReviewState | None = None,
    labels: frozenset[str] = frozenset(),
) -> Issue:
    """A pull request somebody opened, as the issues stream and the pulls endpoint show it."""
    item = Issue(number, "Retry uploads", "Fixes #3.", "open", OWNER, labels, True, T0, T0)
    f.host.issues[number] = item
    f.host.pulls[number] = PullRequest(
        number,
        "head-1",
        f"https://github.com/{REPO}/pull/{number}",
        "clean",
        title="Retry uploads",
        head_ref=branch,
        head_repository=repository,
        base_ref="main",
        draft=draft,
        maintainer_can_modify=can_modify,
        author=OWNER.login,
    )
    f.host.pull_bodies[number] = (branch, "Retry uploads")
    f.host.head_of[branch] = "head-1"
    f.host.check_results = [checks if checks is not None else []]
    if review is not None:
        f.host.reviews[number] = review
    f.polls.append(replace(f.polls.pop(), issues=(item,)) if f.polls else _poll(item))
    return item


def _poll(item: Issue):  # type: ignore[no-untyped-def]
    from github_issue_worker.application.poller import PollResult

    return PollResult(issues=(item,))


def red() -> list[Check]:
    return [Check("tests", "completed", "failure", "3 failed")]


def test_given_pull_requests_switched_off_when_one_arrives_then_nothing_happens() -> None:
    f = Fixture([slot("claude")])
    opened(f, checks=red())
    f.manager.tick()
    assert not f.runs and f.host.worker_label == {} and f.store.current(7) is None


def test_given_a_pull_request_with_red_checks_when_on_then_it_is_claimed_on_its_own_branch() -> (
    None
):
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    opened(f, checks=red())
    f.manager.tick()
    assert f.runs == [Recorded("claude", 7, RunKind.PULL_REQUEST)]
    claim = f.store.current(7)
    assert claim is not None and claim.branch == "retry-uploads"


def test_given_running_checks_when_polled_then_it_waits_and_is_claimed_once_they_fail() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    opened(f, checks=[Check("tests", "in_progress", None)])
    f.manager.tick()
    assert not f.runs and f.store.current(7) is None

    f.host.check_results = [red()]
    f.manager.tick()  # nothing new is polled: the pull request stayed pending
    assert f.runs == [Recorded("claude", 7, RunKind.PULL_REQUEST)]


def test_given_unresolved_review_threads_when_on_then_they_are_the_work() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    thread = ReviewThread("T1", "src/upload.py", 42, "reviewer", "Retry only on 5xx.")
    opened(f, review=ReviewState("CHANGES_REQUESTED", (thread,)))
    f.manager.tick()
    assert f.runs == [Recorded("claude", 7, RunKind.PULL_REQUEST)]


def test_given_a_green_pull_request_without_concerns_when_on_then_it_is_claimed_to_merge() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    opened(f, checks=[Check("tests", "completed", "success")])
    f.manager.tick()
    assert f.runs == [Recorded("claude", 7, RunKind.PULL_REQUEST)]


def test_given_a_fork_without_maintainer_edits_when_on_then_it_is_unserved_and_told_once() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    item = opened(f, repository="someone/ShiftDiff", checks=red())
    f.manager.tick()
    assert not f.runs and f.host.worker_label[7] == "worker:unserved"
    told = [body for number, body in f.host.posted if number == 7]
    assert len(told) == 1 and "allow edits by maintainers" in told[0]

    f.polls.append(_poll(replace(item, labels=frozenset({"worker:unserved"}))))
    f.manager.tick()
    assert len([1 for number, _ in f.host.posted if number == 7]) == 1


def test_given_the_managers_own_pull_request_when_on_then_it_is_left_to_its_issue() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    opened(f, branch="issue/3-retry-uploads", checks=red())
    f.manager.tick()
    assert not f.runs and f.host.worker_label == {}


def test_given_a_draft_when_on_then_it_is_left_alone() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    opened(f, draft=True, checks=red())
    f.manager.tick()
    assert not f.runs and f.host.worker_label == {}


def test_given_labeled_mode_when_the_pull_request_lacks_the_label_then_it_is_left_alone() -> None:
    f = Fixture([slot("claude")])
    f.watch.pull_requests = True
    f.watch.mode = "labeled"
    opened(f, checks=red())
    f.manager.tick()
    assert not f.runs
