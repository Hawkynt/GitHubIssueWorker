"""The coordinator: claims, renews, moves, releases and fences claims (F11.1, F11.3 to F11.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState, Holder
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

    def claim(self, issue: int, holder: Holder, branch: str) -> ClaimHandle | None:
        """Claim ``issue`` for ``holder``, or None when it is held or another claimant won."""
        now = self.clock.now()
        current = self.store.read(issue)
        try:
            if current is None:
                fresh = self.policy.new_claim(issue=issue, holder=holder, now=now, branch=branch)
                return ClaimHandle(self.store.create(fresh), fresh)

            if not self.policy.may_claim(current.claim, now):
                return None

            taken = self.policy.taken_over(current.claim, holder, now)
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

    def pause(self, handle: ClaimHandle, until: datetime) -> ClaimHandle:
        now = self.clock.now()
        paused = self.policy.renewed(handle.claim, now, state=ClaimState.PAUSED, paused_until=until)
        return self._write(handle, paused)

    def resume(self, handle: ClaimHandle) -> ClaimHandle:
        now = self.clock.now()
        working = self.policy.renewed(
            handle.claim, now, state=ClaimState.WORKING, paused_until=None
        )
        return self._write(handle, working)

    def move(self, handle: ClaimHandle, holder: Holder) -> ClaimHandle:
        """Hand the claim to another worker in one compare-and-swap (local hand-off)."""
        return self._write(handle, self.policy.taken_over(handle.claim, holder, self.clock.now()))

    def release(self, handle: ClaimHandle) -> ClaimHandle:
        return self._finish(handle, ClaimState.RELEASED)

    def complete(self, handle: ClaimHandle) -> ClaimHandle:
        return self._finish(handle, ClaimState.DONE)

    def _finish(self, handle: ClaimHandle, state: ClaimState) -> ClaimHandle:
        now = self.clock.now()
        finished = handle.claim.replace(state=state, renewed_at=now, paused_until=None)
        return self._write(handle, finished)

    def _write(self, handle: ClaimHandle, claim: Claim) -> ClaimHandle:
        try:
            return ClaimHandle(self.store.replace(handle.version, claim), claim)
        except ClaimConflict as conflict:
            raise ClaimLost(f"issue {claim.issue}: {conflict}") from conflict
