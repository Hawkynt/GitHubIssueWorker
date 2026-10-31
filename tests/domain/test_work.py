from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, ConsumedThrough, Holder
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.trust import is_trusted
from github_issue_worker.domain.work import RunKind, WorkContext, new_input, pending_run

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
OWNER = Actor(login="Hawkynt")
STRANGER = Actor(login="mallory")
WATCHED_SINCE = T0 - timedelta(days=1)

ISSUE = Issue(
    number=42,
    title="Retry on timeout",
    body="The client gives up after one timeout.",
    state="open",
    author=OWNER,
    labels=frozenset({"bug"}),
    is_pull_request=False,
    created_at=T0,
    updated_at=T0,
)


def comment(id: int, author: Actor = OWNER, body: str = "please also log it") -> Comment:
    return Comment(id=id, issue=42, author=author, body=body, created_at=T0, updated_at=T0)


def context(**changes: object) -> WorkContext:
    base = WorkContext(
        issue=ISSUE,
        claim=None,
        comments=(),
        trusted=lambda actor: actor == OWNER,
        mode="all",
        watched_since=WATCHED_SINCE,
        policy=POLICY,
        now=T0 + timedelta(hours=1),
    )
    return replace(base, **changes)


def held(
    state: ClaimState = ClaimState.WORKING, step: str = "implement", consumed: int | None = None
):  # type: ignore[no-untyped-def]
    claim = POLICY.new_claim(issue=42, holder=Holder("m", "w"), now=T0, branch="issue/42-x")
    return claim.replace(state=state, step=step, consumed_through=ConsumedThrough(comment=consumed))


def test_a_new_issue_by_a_trusted_author_needs_a_first_run() -> None:
    assert pending_run(context()) is RunKind.FIRST


def test_an_issue_from_before_the_watch_started_is_left_alone() -> None:
    old = replace(ISSUE, created_at=WATCHED_SINCE - timedelta(days=30))
    assert pending_run(context(issue=old)) is None


def test_a_trusted_comment_on_an_old_issue_starts_a_first_run() -> None:
    old = replace(ISSUE, created_at=WATCHED_SINCE - timedelta(days=30))
    assert pending_run(context(issue=old, comments=(comment(5),))) is RunKind.FIRST


@pytest.mark.parametrize(
    "issue",
    [
        replace(ISSUE, is_pull_request=True),
        replace(ISSUE, state="closed"),
        replace(ISSUE, labels=frozenset({"bug", "worker:skip"})),
        replace(ISSUE, author=STRANGER),
    ],
    ids=["pull-request", "closed", "skip-label", "untrusted-author"],
)
def test_issues_that_are_never_worked(issue: Issue) -> None:
    assert pending_run(context(issue=issue, comments=(comment(5),))) is None


def test_labeled_mode_needs_the_worker_label() -> None:
    assert pending_run(context(mode="labeled")) is None
    labelled = replace(ISSUE, labels=frozenset({"worker"}))
    assert pending_run(context(mode="labeled", issue=labelled)) is RunKind.FIRST


@pytest.mark.parametrize("mode", ["all", "labeled"])
def test_worker_skip_is_honoured_in_every_mode_even_with_the_worker_label(
    mode: str,
) -> None:
    skipped = replace(ISSUE, labels=frozenset({"worker", "worker:skip"}))
    assert pending_run(context(mode=mode, issue=skipped)) is None
    assert pending_run(context(mode=mode, issue=skipped, claim=held(ClaimState.RELEASED))) is None
    assert pending_run(context(mode=mode, issue=skipped, claim=held(ClaimState.DONE))) is None


def test_a_held_claim_is_left_to_its_holder() -> None:
    assert pending_run(context(claim=held(), comments=(comment(5),))) is None
    assert pending_run(context(claim=held(ClaimState.PAUSED))) is None


def test_an_abandoned_claim_needs_a_continuation() -> None:
    later = T0 + timedelta(days=8)
    assert pending_run(context(claim=held(), now=later)) is RunKind.CONTINUATION


def test_an_open_issue_with_a_done_claim_was_reopened() -> None:
    assert pending_run(context(claim=held(ClaimState.DONE, step="close"))) is RunKind.REOPEN


def test_a_released_hand_off_continues_without_new_input() -> None:
    assert pending_run(context(claim=held(ClaimState.RELEASED))) is RunKind.CONTINUATION


@pytest.mark.parametrize("step", ["needs-input", "awaiting-review", "failed", "split"])
def test_a_waiting_issue_waits_for_a_new_trusted_comment(step: str) -> None:
    waiting = held(ClaimState.RELEASED, step=step, consumed=10)
    assert pending_run(context(claim=waiting)) is None
    assert pending_run(context(claim=waiting, comments=(comment(9),))) is None  # already consumed
    assert pending_run(context(claim=waiting, comments=(comment(11, STRANGER),))) is None
    assert pending_run(context(claim=waiting, comments=(comment(11),))) is RunKind.REWORK


def test_own_output_is_never_new_input() -> None:
    marker = comment(11, body="Question for you <!-- ghiw:question -->")
    assert new_input(context(comments=(marker, comment(12), comment(13, STRANGER)))) == (
        comment(12),
    )


@pytest.mark.parametrize(
    ("actor", "role", "allowlist", "expected"),
    [
        (Actor("alice"), "admin", (), True),
        (Actor("alice"), "maintain", (), True),
        (Actor("alice"), "write", (), True),
        (Actor("alice"), "triage", (), False),
        (Actor("alice"), "read", (), False),
        (Actor("alice"), None, (), False),
        (Actor("alice"), None, ("Alice",), True),
        (Actor("dependabot[bot]", is_bot=True), "write", ("dependabot[bot]",), False),
    ],
)
def test_trust_needs_write_access_or_the_allowlist_and_never_a_bot(
    actor: Actor, role: str | None, allowlist: tuple[str, ...], expected: bool
) -> None:
    assert is_trusted(actor, role, allowlist) is expected
