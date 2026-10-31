"""The coordinator: claims, renews, moves, releases and fences claims (F11.1, F11.3 to F11.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_issue_worker.domain.claim import (
    Claim,
    ClaimPolicy,
    ClaimState,
    ConsumedThrough,
    Holder,
    RunEntry,
)
from github_issue_worker.ports.claims import ClaimConflict, ClaimStore
from github_issue_worker.ports.clock import Clock


class ClaimLost(Exception):
    """The claim is no longer this holder's. The run must stop and discard its local work."""


@dataclass(frozen=True, slots=True)
class ClaimHandle:
    """A claim as this holder last wrote it. Every operation returns the next handle."""

    version: str
    claim: Claim


@dataclass(frozen=True, slots=True)
class Coordinator:
    store: ClaimStore
    clock: Clock
    policy: ClaimPolicy

    def claim(self, issue: int, holder: Holder, branch: str, model: str = "") -> ClaimHandle | None:
        """Claim ``issue`` for ``holder``, or None when it is held or another claimant won."""
        now = self.clock.now()
        current = self.store.read(issue)
        try:
            if current is None:
                fresh = self.policy.new_claim(
                    issue=issue, holder=holder, now=now, branch=branch, model=model
                )
                return ClaimHandle(self.store.create(fresh), fresh)

            if not self.policy.may_claim(current.claim, now):
                return None

            taken = self.policy.taken_over(current.claim, holder, now, model=model)
            return ClaimHandle(self.store.replace(current.version, taken), taken)
        except ClaimConflict:
            return None

    def renew(self, handle: ClaimHandle, step: str | None = None) -> ClaimHandle:
        changes = {"step": step} if step is not None else {}
        return self._write(handle, self.policy.renewed(handle.claim, self.clock.now(), **changes))

    def fence(self, handle: ClaimHandle) -> ClaimHandle:
        """Confirm the claim is still this holder's before an outward write (F11.4).

        A claim past its expiry (the machine was off) is renewed first, and only a successful
        renewal lets the write go ahead.
        """
        current = self.store.read(handle.claim.issue)
        if current is None or current.version != handle.version:
            raise ClaimLost(f"issue {handle.claim.issue}: the claim changed since it was written")
        if self.clock.now() >= handle.claim.expires_at:
            return self.renew(handle)
        return handle

    def set_merge_conflict(self, handle: ClaimHandle, conflict: bool) -> ClaimHandle:
        """Checkpoint display state without changing the claim's ownership or lifecycle."""
        if handle.claim.merge_conflict == conflict:
            return handle
        return self._write(handle, handle.claim.replace(merge_conflict=conflict))

    def pause(self, handle: ClaimHandle, until: datetime, reason: str = "") -> ClaimHandle:
        now = self.clock.now()
        paused = self.policy.renewed(
            handle.claim, now, state=ClaimState.PAUSED, paused_until=until, wait_reason=reason
        )
        return self._write(handle, paused)

    def resume(self, handle: ClaimHandle) -> ClaimHandle:
        now = self.clock.now()
        working = self.policy.renewed(
            handle.claim, now, state=ClaimState.WORKING, paused_until=None, wait_reason=""
        )
        return self._write(handle, working)

    def record_run(self, issue: int, entry: RunEntry, attempts: int = 5) -> None:
        """Append a finished run to the issue's claim, whoever holds it now (fleet history).

        A compare-and-swap like every claim write, retried when somebody wrote in between; a run
        already recorded is not recorded twice.
        """
        for _ in range(attempts):
            current = self.store.read(issue)
            if current is None or any(run.key == entry.key for run in current.claim.runs):
                return
            try:
                self.store.replace(
                    current.version, current.claim.replace(runs=(*current.claim.runs, entry))
                )
                return
            except ClaimConflict:
                continue
        raise ClaimLost(f"issue {issue}: the run could not be recorded in the claim")

    def move(self, handle: ClaimHandle, holder: Holder, model: str | None = None) -> ClaimHandle:
        """Hand the claim to another worker in one compare-and-swap (local hand-off)."""
        wears = handle.claim.model if model is None else model
        taken = self.policy.taken_over(handle.claim, holder, self.clock.now(), model=wears)
        return self._write(handle, taken)

    def release(
        self, handle: ClaimHandle, step: str | None = None, consumed: int | None = None
    ) -> ClaimHandle:
        """Free the issue at once. ``step`` says what it waits for (e.g. ``needs-input``)."""
        return self._finish(handle, ClaimState.RELEASED, step, consumed)

    def complete(self, handle: ClaimHandle, consumed: int | None = None) -> ClaimHandle:
        return self._finish(handle, ClaimState.DONE, "done", consumed)

    def _finish(
        self, handle: ClaimHandle, state: ClaimState, step: str | None, consumed: int | None
    ) -> ClaimHandle:
        """``consumed`` advances consumedThrough in the same write that ends the run (F11.7)."""
        claim = handle.claim
        through = claim.consumed_through
        if consumed is not None and (through.comment is None or consumed > through.comment):
            through = ConsumedThrough(comment=consumed, review=through.review)
        finished = claim.replace(
            state=state,
            step=step or claim.step,
            renewed_at=self.clock.now(),
            paused_until=None,
            consumed_through=through,
        )
        return self._write(handle, finished)

    def _write(self, handle: ClaimHandle, claim: Claim) -> ClaimHandle:
        try:
            return ClaimHandle(self.store.replace(handle.version, claim), claim)
        except ClaimConflict as conflict:
            raise ClaimLost(f"issue {claim.issue}: {conflict}") from conflict
