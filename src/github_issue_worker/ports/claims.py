"""Where claims live, as a compare-and-swap store (F11.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from github_issue_worker.domain.claim import Claim


class ClaimConflict(Exception):
    """The claim was not what the writer expected: somebody else changed it first."""


class ClaimStoreError(Exception):
    """The store could not be reached or answered unexpectedly. Says nothing about the claim."""


@dataclass(frozen=True, slots=True)
class StoredClaim:
    """A claim together with the version it was read at, for the next compare-and-swap."""

    version: str
    claim: Claim


class ClaimStore(Protocol):
    """Every write is conditional on the version last seen, and fails with `ClaimConflict`."""

    def issues(self) -> list[int]:
        """Every issue that has a claim ref, whatever its state."""
        ...

    def read(self, issue: int) -> StoredClaim | None:
        """The current claim of ``issue``, or None when there is none."""
        ...

    def create(self, claim: Claim) -> str:
        """Store ``claim`` if the issue has no claim yet. Returns the new version."""
        ...

    def replace(self, expected: str, claim: Claim) -> str:
        """Store ``claim`` if the current version is ``expected``. Returns the new version."""
        ...

    def delete(self, issue: int, expected: str) -> None:
        """Remove the claim of ``issue`` if the current version is ``expected``."""
        ...

    def history(self, issue: int, limit: int) -> list[Claim]:
        """The newest ``limit`` versions of the claim of ``issue``, oldest first (F14.6).

        Empty when the issue has no claim.
        """
        ...
