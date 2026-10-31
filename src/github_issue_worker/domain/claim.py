"""Claims: which worker works an issue, and the rules for when somebody else may take it.

A claim is valid for ``validity`` after its last renewal. Another worker may take it over only once
``validity + grace`` has passed without a renewal, so a holder returning right at the edge of its
validity is never raced. Released and done claims are free at once.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class ClaimState(StrEnum):
    """Lifecycle of a claim. Only ``working`` and ``paused`` hold the issue."""

    WORKING = "working"
    PAUSED = "paused"
    RELEASED = "released"
    DONE = "done"

    @property
    def holds(self) -> bool:
        return self in (ClaimState.WORKING, ClaimState.PAUSED)


@dataclass(frozen=True, slots=True)
class Holder:
    """A worker of a manager: the only kind of party that can hold a claim."""

    manager: str
    worker: str

    def __str__(self) -> str:
        return f"{self.manager}/{self.worker}"


@dataclass(frozen=True, slots=True)
class ConsumedThrough:
    """The newest comment and review already handled by a completed run."""

    comment: int | None = None
    review: int | None = None


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RunEntry:
    """One finished run on the issue, kept in the claim so every manager reads the same history."""

    manager: str
    worker: str
    agent: str
    model: str
    kind: str
    verdict: str
    started: datetime
    finished: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "manager": self.manager,
            "worker": self.worker,
            "agent": self.agent,
            "model": self.model,
            "kind": self.kind,
            "verdict": self.verdict,
            "started": _timestamp(self.started),
            "finished": _timestamp(self.finished),
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> RunEntry:
        return RunEntry(
            manager=str(data["manager"]),
            worker=str(data["worker"]),
            agent=str(data.get("agent", "")),
            model=str(data.get("model", "")),
            kind=str(data.get("kind", "")),
            verdict=str(data["verdict"]),
            started=_parse_timestamp(data["started"]),
            finished=_parse_timestamp(data["finished"]),
        )

    @property
    def key(self) -> tuple[str, str, datetime]:
        """Which run this is: claims store times to the second, local history finer."""
        return (self.manager, self.worker, self.started.replace(microsecond=0))


@dataclass(frozen=True, slots=True)
class Claim:
    """The content of ``refs/ghiw/claims/<issue>``."""

    issue: int
    holder: Holder
    generation: int
    state: ClaimState
    step: str
    claimed_at: datetime
    renewed_at: datetime
    expires_at: datetime
    branch: str
    paused_until: datetime | None = None
    consumed_through: ConsumedThrough = ConsumedThrough()
    takeovers: int = 0
    merge_conflict: bool = False
    wait_reason: str = ""
    model: str = ""
    """The model the holder runs, normalised, so other managers can show it."""
    runs: tuple[RunEntry, ...] = ()
    """Every finished run on the issue, by any manager, oldest first."""

    def replace(self, **changes: Any) -> Claim:
        return dataclasses.replace(self, **changes)

    def to_json(self) -> dict[str, Any]:
        return {
            **({"mergeConflict": True} if self.merge_conflict else {}),
            **({"waitReason": self.wait_reason} if self.wait_reason else {}),
            **({"model": self.model} if self.model else {}),
            **({"runs": [run.to_json() for run in self.runs]} if self.runs else {}),
            "issue": self.issue,
            "holder": {"manager": self.holder.manager, "worker": self.holder.worker},
            "generation": self.generation,
            "state": self.state.value,
            "step": self.step,
            "claimedAt": _timestamp(self.claimed_at),
            "renewedAt": _timestamp(self.renewed_at),
            "expiresAt": _timestamp(self.expires_at),
            "pausedUntil": _timestamp(self.paused_until) if self.paused_until else None,
            "consumedThrough": {
                "comment": self.consumed_through.comment,
                "review": self.consumed_through.review,
            },
            "takeovers": self.takeovers,
            "branch": self.branch,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> Claim:
        paused_until = data.get("pausedUntil")
        consumed = data.get("consumedThrough") or {}
        return Claim(
            merge_conflict=data.get("mergeConflict", False),
            wait_reason=data.get("waitReason", ""),
            model=str(data.get("model", "")),
            runs=tuple(RunEntry.from_json(run) for run in data.get("runs") or ()),
            issue=int(data["issue"]),
            holder=Holder(manager=data["holder"]["manager"], worker=data["holder"]["worker"]),
            generation=int(data["generation"]),
            state=ClaimState(data["state"]),
            step=data["step"],
            claimed_at=_parse_timestamp(data["claimedAt"]),
            renewed_at=_parse_timestamp(data["renewedAt"]),
            expires_at=_parse_timestamp(data["expiresAt"]),
            paused_until=_parse_timestamp(paused_until) if paused_until else None,
            consumed_through=ConsumedThrough(
                comment=consumed.get("comment"), review=consumed.get("review")
            ),
            takeovers=int(data.get("takeovers", 0)),
            branch=data["branch"],
        )


@dataclass(frozen=True, slots=True)
class ClaimPolicy:
    """The timing rules of claims (F11.3)."""

    validity: timedelta
    grace: timedelta

    def new_claim(
        self, issue: int, holder: Holder, now: datetime, branch: str, model: str = ""
    ) -> Claim:
        return Claim(
            model=model,
            issue=issue,
            holder=holder,
            generation=1,
            state=ClaimState.WORKING,
            step="triage",
            claimed_at=now,
            renewed_at=now,
            expires_at=now + self.validity,
            branch=branch,
        )

    def is_abandoned(self, claim: Claim, now: datetime) -> bool:
        """A held claim nobody renewed for validity plus grace."""
        return claim.state.holds and now >= claim.renewed_at + self.validity + self.grace

    def may_claim(self, claim: Claim | None, now: datetime) -> bool:
        """Whether a worker other than the holder may claim the issue now."""
        return claim is None or not claim.state.holds or self.is_abandoned(claim, now)

    def renewed(self, claim: Claim, now: datetime, **changes: Any) -> Claim:
        return claim.replace(renewed_at=now, expires_at=now + self.validity, **changes)

    def taken_over(self, claim: Claim, holder: Holder, now: datetime, model: str = "") -> Claim:
        """The claim after ``holder`` takes it, keeping the issue's branch, consumed events and
        finished runs, and wearing the new holder's model."""
        return claim.replace(
            holder=holder,
            model=model,
            generation=claim.generation + 1,
            state=ClaimState.WORKING,
            claimed_at=now,
            renewed_at=now,
            expires_at=now + self.validity,
            paused_until=None,
            wait_reason="",
            takeovers=claim.takeovers + (1 if self.is_abandoned(claim, now) else 0),
        )
