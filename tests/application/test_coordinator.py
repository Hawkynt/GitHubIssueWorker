from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.application.coordinator import ClaimLost, Coordinator
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from tests.fakes import FakeClock, InMemoryClaimStore

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
CODEX = Holder(manager="office-pc", worker="codex")
CLAUDE = Holder(manager="laptop", worker="claude")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def store() -> InMemoryClaimStore:
    return InMemoryClaimStore()


def coordinator(store: InMemoryClaimStore, clock: FakeClock) -> Coordinator:
    return Coordinator(store=store, clock=clock, policy=POLICY)


def test_the_first_claimant_gets_the_issue(store: InMemoryClaimStore, clock: FakeClock) -> None:
    handle = coordinator(store, clock).claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    assert handle.claim.holder == CODEX
    assert store.current(42) == handle.claim


def test_a_held_claim_cannot_be_claimed(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coordinator(store, clock).claim(42, CODEX, branch="issue/42-x")
    clock.advance(timedelta(days=7, hours=12))
    assert coordinator(store, clock).claim(42, CLAUDE, branch="issue/42-x") is None


def test_an_abandoned_claim_is_taken_over_after_eight_days(
    store: InMemoryClaimStore, clock: FakeClock
) -> None:
    coordinator(store, clock).claim(42, CODEX, branch="issue/42-x")
    clock.advance(timedelta(days=8))
    handle = coordinator(store, clock).claim(42, CLAUDE, branch="issue/42-x")
    assert handle is not None
    assert handle.claim.holder == CLAUDE
    assert handle.claim.takeovers == 1


def test_losing_a_race_returns_none(store: InMemoryClaimStore, clock: FakeClock) -> None:
    store.interleave_before_write = lambda: store.force_create(
        POLICY.new_claim(issue=42, holder=CLAUDE, now=T0, branch="issue/42-x")
    )
    assert coordinator(store, clock).claim(42, CODEX, branch="issue/42-x") is None
    current = store.current(42)
    assert current is not None and current.holder == CLAUDE


def test_renewal_extends_the_claim(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    clock.advance(timedelta(days=6))
    renewed = coord.renew(handle, step="push")
    assert renewed.claim.step == "push"
    assert renewed.claim.expires_at == T0 + timedelta(days=13)


def test_renewing_a_claim_someone_took_over_raises(
    store: InMemoryClaimStore, clock: FakeClock
) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    clock.advance(timedelta(days=9))
    assert coord.claim(42, CLAUDE, branch="issue/42-x") is not None
    with pytest.raises(ClaimLost):
        coord.renew(handle)


def test_fencing_confirms_a_current_claim(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    assert coord.fence(handle) == handle


def test_fencing_renews_a_claim_past_its_expiry(
    store: InMemoryClaimStore, clock: FakeClock
) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    clock.advance(timedelta(days=7, hours=6))  # the machine was off for over a week
    fenced = coord.fence(handle)
    assert fenced.claim.renewed_at == clock.now()


def test_fencing_a_lost_claim_raises(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    clock.advance(timedelta(days=8))
    coord.claim(42, CLAUDE, branch="issue/42-x")
    with pytest.raises(ClaimLost):
        coord.fence(handle)


def test_release_frees_the_issue_at_once(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    coord.release(handle)
    current = store.current(42)
    assert current is not None and current.state is ClaimState.RELEASED
    assert coord.claim(42, CLAUDE, branch="issue/42-x") is not None


def test_a_local_hand_off_moves_the_claim_in_one_step(
    store: InMemoryClaimStore, clock: FakeClock
) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    local_claude = Holder(manager="office-pc", worker="claude")
    moved = coord.move(handle, local_claude)
    assert moved.claim.holder == local_claude
    assert moved.claim.generation == 2
    assert moved.claim.takeovers == 0
    with pytest.raises(ClaimLost):
        coord.renew(handle)


def test_complete_marks_the_claim_done(store: InMemoryClaimStore, clock: FakeClock) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    coord.complete(handle)
    current = store.current(42)
    assert current is not None and current.state is ClaimState.DONE


def test_pause_records_the_reset_and_keeps_the_claim(
    store: InMemoryClaimStore, clock: FakeClock
) -> None:
    coord = coordinator(store, clock)
    handle = coord.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    reset = T0 + timedelta(hours=5)
    paused = coord.pause(handle, until=reset)
    assert paused.claim.state is ClaimState.PAUSED
    assert paused.claim.paused_until == reset
    assert coord.claim(42, CLAUDE, branch="issue/42-x") is None
    resumed = coord.resume(paused)
    assert resumed.claim.state is ClaimState.WORKING
    assert resumed.claim.paused_until is None
