from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.domain.claim import (
    Claim,
    ClaimPolicy,
    ClaimState,
    ConsumedThrough,
    Holder,
    RunEntry,
    takeover_evidence,
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


def _run(started_minutes: int, verdict: str = "merged", manager: str = "m1") -> RunEntry:
    started = T0 + timedelta(minutes=started_minutes)
    return RunEntry(
        manager=manager,
        worker="claude",
        agent="claude",
        model="Claude Opus 5.5",
        kind="first",
        verdict=verdict,
        started=started,
        finished=started + timedelta(minutes=10),
    )


def test_the_holders_model_and_the_finished_runs_travel_with_the_claim() -> None:
    claim = POLICY.new_claim(1, Holder("m1", "claude"), T0, "issue/1-x", model="Claude Opus 5.5")
    claim = claim.replace(runs=(_run(0, "needs-input"), _run(30)))
    again = Claim.from_json(claim.to_json())
    assert again == claim and again.model == "Claude Opus 5.5" and len(again.runs) == 2
    old = claim.to_json()
    del old["model"], old["runs"]
    assert Claim.from_json(old).runs == () and Claim.from_json(old).model == ""  # older claims


def test_a_takeover_keeps_the_issues_runs_but_wears_the_new_holders_model() -> None:
    claim = POLICY.new_claim(1, Holder("m1", "claude"), T0, "issue/1-x", model="Claude Opus 5.5")
    claim = claim.replace(runs=(_run(0),))
    taken = POLICY.taken_over(
        claim, Holder("m2", "codex"), T0 + timedelta(days=9), model="GPT 6.1 Sol"
    )
    assert taken.model == "GPT 6.1 Sol" and taken.runs == claim.runs


STRANGER = Holder(manager="laptop-01ab", worker="claude")
ABANDONED = T0 + timedelta(days=8)


def test_given_max_takeovers_reached_when_the_claim_is_abandoned_then_it_has_stalled() -> None:
    policy = ClaimPolicy(timedelta(days=7), timedelta(days=1), max_takeovers=3)
    assert not policy.stalled(claim(takeovers=2), ABANDONED)
    assert policy.stalled(claim(takeovers=3), ABANDONED)
    assert not policy.stalled(claim(takeovers=3), ABANDONED - timedelta(minutes=1))
    assert not policy.stalled(claim(takeovers=3, state=ClaimState.RELEASED), ABANDONED)


def test_given_max_takeovers_takeovers_without_progress_then_the_next_abandonment_stalls() -> None:
    policy = ClaimPolicy(timedelta(days=7), timedelta(days=1), max_takeovers=2)
    current, now = claim(), T0
    for _ in range(2):
        now += timedelta(days=8)
        assert not policy.stalled(current, now)
        current = policy.taken_over(current, STRANGER, now)
    assert policy.stalled(current, now + timedelta(days=8))


def test_given_up_releases_the_stalled_claim_at_step_failed() -> None:
    stalled = claim(takeovers=3, step="verify")
    failed = POLICY.given_up(stalled, ABANDONED)
    assert failed.state is ClaimState.RELEASED
    assert failed.step == "failed"
    assert failed.takeovers == 0
    assert failed.holder == HOLDER
    assert failed.renewed_at == ABANDONED


def test_the_takeover_evidence_names_count_step_holder_renewal_and_runs() -> None:
    run = RunEntry(
        "laptop-01ab", "claude", "claude", "Claude Opus 5.5", "first", "needs-input", T0, T0
    )
    stalled = claim(takeovers=3, step="verify", runs=(run,))
    text = takeover_evidence(stalled, max_takeovers=3)
    assert "3 takeovers" in text
    assert "`verify`" in text
    assert "office-pc-7f3a/codex" in text
    assert "2026-10-30T12:00:00Z" in text
    assert "laptop-01ab/claude" in text and "needs-input" in text
