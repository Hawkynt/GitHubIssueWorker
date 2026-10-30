from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.domain.claim import (
    Claim,
    ClaimPolicy,
    ClaimState,
    ConsumedThrough,
    Holder,
)

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
HOLDER = Holder(manager="office-pc-7f3a", worker="codex")


def claim(**changes: object) -> Claim:
    base = POLICY.new_claim(issue=42, holder=HOLDER, now=T0, branch="issue/42-retry")
    return base.replace(**changes) if changes else base


def test_a_new_claim_is_valid_for_the_validity_period() -> None:
    fresh = claim()
    assert fresh.generation == 1
    assert fresh.state is ClaimState.WORKING
    assert fresh.claimed_at == fresh.renewed_at == T0
    assert fresh.expires_at == T0 + timedelta(days=7)


@pytest.mark.parametrize(
    ("elapsed", "free"),
    [
        (timedelta(days=6, hours=23), False),
        (timedelta(days=7, hours=1), False),  # expired, but still inside the grace day
        (timedelta(days=7, hours=23, minutes=59), False),
        (timedelta(days=8), True),
        (timedelta(days=30), True),
    ],
)
def test_another_worker_may_take_over_only_after_validity_plus_grace(
    elapsed: timedelta, free: bool
) -> None:
    assert POLICY.is_abandoned(claim(), T0 + elapsed) is free
    assert POLICY.may_claim(claim(), T0 + elapsed) is free


@pytest.mark.parametrize("state", [ClaimState.WORKING, ClaimState.PAUSED])
def test_held_states_are_not_free(state: ClaimState) -> None:
    assert not POLICY.may_claim(claim(state=state), T0 + timedelta(hours=1))


@pytest.mark.parametrize("state", [ClaimState.RELEASED, ClaimState.DONE])
def test_released_and_done_claims_are_free_at_once(state: ClaimState) -> None:
    assert POLICY.may_claim(claim(state=state), T0)


def test_no_claim_at_all_is_free() -> None:
    assert POLICY.may_claim(None, T0)


def test_renewal_moves_both_renewal_and_expiry() -> None:
    later = T0 + timedelta(days=3)
    renewed = POLICY.renewed(claim(), later)
    assert renewed.renewed_at == later
    assert renewed.expires_at == later + timedelta(days=7)
    assert renewed.generation == 1


def test_a_takeover_increments_generation_and_takeovers() -> None:
    later = T0 + timedelta(days=9)
    other = Holder(manager="laptop-01ab", worker="claude")
    taken = POLICY.taken_over(claim(), other, later)
    assert taken.holder == other
    assert taken.generation == 2
    assert taken.takeovers == 1
    assert taken.claimed_at == taken.renewed_at == later
    assert taken.state is ClaimState.WORKING
    assert taken.branch == "issue/42-retry"


def test_reclaiming_a_released_claim_is_not_a_takeover() -> None:
    later = T0 + timedelta(hours=2)
    reclaimed = POLICY.taken_over(claim(state=ClaimState.RELEASED), HOLDER, later)
    assert reclaimed.generation == 2
    assert reclaimed.takeovers == 0


def test_json_round_trip_uses_the_documented_keys() -> None:
    original = claim(
        step="implement",
        paused_until=T0 + timedelta(hours=3),
        consumed_through=ConsumedThrough(comment=2398177412, review=None),
    )
    data = original.to_json()
    assert set(data) == {
        "issue",
        "holder",
        "generation",
        "state",
        "step",
        "claimedAt",
        "renewedAt",
        "expiresAt",
        "pausedUntil",
        "consumedThrough",
        "takeovers",
        "branch",
    }
    assert data["holder"] == {"manager": "office-pc-7f3a", "worker": "codex"}
    assert data["renewedAt"] == "2026-10-30T12:00:00Z"
    assert Claim.from_json(data) == original


def test_conflict_and_wait_reason_survive_checkpoints_without_migrating_old_claims() -> None:
    old = claim().to_json()
    assert not Claim.from_json(old).merge_conflict
    assert Claim.from_json(old).wait_reason == ""
    blocked = claim(merge_conflict=True, wait_reason="Rate limit")
    assert Claim.from_json(blocked.to_json()) == blocked
    assert POLICY.taken_over(blocked, HOLDER, T0).merge_conflict


def test_taking_over_a_paused_claim_clears_the_obsolete_wait_reason() -> None:
    waiting = claim(state=ClaimState.PAUSED, wait_reason="Token allowance", paused_until=T0)
    resumed = POLICY.taken_over(waiting, HOLDER, T0)
    assert resumed.wait_reason == "" and resumed.paused_until is None
