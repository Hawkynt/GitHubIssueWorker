"""Pure rendering of watched repositories and their current issues (F1.4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from github_issue_worker.application.status import table
from github_issue_worker.domain.claim import Claim, ClaimPolicy
from github_issue_worker.domain.issue import Issue
from github_issue_worker.domain.issue_state import ISSUE_STATES, issue_state


@dataclass(frozen=True, slots=True)
class RepositoryListing:
    repository: str
    last_polled_at: datetime | None
    issues: tuple[Issue, ...]
    claims: tuple[Claim, ...]


def render_list(
    repositories: Sequence[RepositoryListing], policy: ClaimPolicy, now: datetime
) -> str:
    if not repositories:
        return "no repositories watched\n"
    sections = []
    for repository in repositories:
        claims = {claim.issue: claim for claim in repository.claims}
        counts = dict.fromkeys(ISSUE_STATES, 0)
        rows = [["ISSUE", "STATE", "HOLDER", "TITLE"]]
        for issue in sorted(repository.issues, key=lambda issue: issue.number):
            if issue.state != "open" or issue.is_pull_request:
                continue
            claim = claims.get(issue.number)
            state = issue_state(issue, claim, policy, now)
            counts[state] += 1
            holder = (
                str(claim.holder)
                if claim and claim.state.holds and not policy.is_abandoned(claim, now)
                else "-"
            )
            rows.append([f"#{issue.number}", state, holder, issue.title.replace("\n", " ")])
        stamp = (
            repository.last_polled_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            if repository.last_polled_at
            else "never polled"
        )
        lines = [repository.repository, f"  last poll: {stamp}"]
        lines.append("  " + " ".join(f"{state}={count}" for state, count in counts.items()))
        lines += table(rows, "  ") if len(rows) > 1 else ["  no current issues"]
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"
