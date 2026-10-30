"""In-memory stand-ins for the ports, for fast application tests."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import datetime, timedelta

from github_issue_worker.domain.claim import Claim
from github_issue_worker.ports.claims import ClaimConflict, StoredClaim


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, by: timedelta) -> None:
        self._now += by


class InMemoryClaimStore:
    """Compare-and-swap semantics of the git claim store, without git."""

    def __init__(self) -> None:
        self._claims: dict[int, StoredClaim] = {}
        self._versions = itertools.count(1)
        self.interleave_before_write: Callable[[], None] | None = None

    def current(self, issue: int) -> Claim | None:
        stored = self._claims.get(issue)
        return stored.claim if stored else None

    def force_create(self, claim: Claim) -> None:
        self._claims[claim.issue] = StoredClaim(f"v{next(self._versions)}", claim)

    def read(self, issue: int) -> StoredClaim | None:
        return self._claims.get(issue)

    def create(self, claim: Claim) -> str:
        self._interleave()
        if claim.issue in self._claims:
            raise ClaimConflict("a claim already exists")
        return self._store(claim)

    def replace(self, expected: str, claim: Claim) -> str:
        self._interleave()
        current = self._claims.get(claim.issue)
        if current is None or current.version != expected:
            raise ClaimConflict("stale version")
        return self._store(claim)

    def delete(self, issue: int, expected: str) -> None:
        self._interleave()
        current = self._claims.get(issue)
        if current is None or current.version != expected:
            raise ClaimConflict("stale version")
        del self._claims[issue]

    def _interleave(self) -> None:
        hook, self.interleave_before_write = self.interleave_before_write, None
        if hook:
            hook()

    def _store(self, claim: Claim) -> str:
        version = f"v{next(self._versions)}"
        self._claims[claim.issue] = StoredClaim(version, claim)
        return version
