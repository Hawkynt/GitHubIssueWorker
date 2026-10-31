"""What ``status`` prints (F10.6, F13.7): workers, accounts and claims as headed, aligned text.

Rendering is pure: the command gathers slots, account limits and claims, and this module turns them
into text against one ``now``, so the same inputs always give the same output.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from github_issue_worker.application.accounts import AccountUsage, account_usage
from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState
from github_issue_worker.domain.model_name import display_model


@dataclass(frozen=True, slots=True)
class RepositoryClaims:
    """Every claim recorded in one watched repository."""

    repository: str
    claims: tuple[Claim, ...]


def span(delta: timedelta) -> str:
    """A duration in its two largest units: ``6d 23h``, ``2h 30m``, ``12m``."""
    minutes = int(abs(delta).total_seconds()) // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def ago(moment: datetime, now: datetime) -> str:
    return f"{span(now - moment)} ago"


def when(moment: datetime, now: datetime) -> str:
    """A moment as UTC time with its distance from now."""
    relative = f"in {span(moment - now)}" if moment > now else ago(moment, now)
    return f"{moment:%Y-%m-%d %H:%M} UTC ({relative})"


def table(rows: Sequence[Sequence[str]], indent: str) -> list[str]:
    """Left-aligned columns two spaces apart, without trailing blanks."""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        (
            indent + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        ).rstrip()
        for row in rows
    ]


def model_of(slot: WorkerSlot | None) -> str:
    """What the worker runs on: the model its last run reported, else the configured one."""
    if slot is None:
        return "-"
    if slot.observed_model:
        return display_model(slot.observed_model)
    return f"{display_model(slot.model)} (configured)" if slot.model else "-"


def _worker_state(slot: WorkerSlot, limited_until: datetime | None, now: datetime) -> str:
    if not slot.available:
        return f"unavailable: {slot.reason}"
    if limited_until:
        return f"limited until {when(limited_until, now)}"
    return "ok"


def _claim_state(claim: Claim, policy: ClaimPolicy, now: datetime) -> str:
    if policy.is_abandoned(claim, now):
        return "abandoned"
    if claim.state is ClaimState.PAUSED and claim.paused_until:
        return f"paused until {when(claim.paused_until, now)}"
    return claim.state.value


def _claims(
    title: str,
    who: str,
    claims: Sequence[Claim],
    show: Callable[[Claim], str],
    policy: ClaimPolicy,
    now: datetime,
    model: Callable[[Claim], str] | None = None,
) -> list[str]:
    rows = [
        [
            "ISSUE",
            who,
            *(["MODEL"] if model else []),
            "STATE",
            "STEP",
            "RENEWED",
            "EXPIRES",
            "BRANCH",
        ]
    ]
    for claim in claims:
        rows.append(
            [
                f"#{claim.issue}",
                show(claim),
                *([model(claim)] if model else []),
                _claim_state(claim, policy, now),
                claim.step,
                ago(claim.renewed_at, now),
                when(claim.expires_at, now),
                claim.branch,
            ]
        )
    return [f"  {title}", *table(rows, "    ")]


def _repository(
    repository: RepositoryClaims,
    manager_id: str,
    policy: ClaimPolicy,
    now: datetime,
    slots: Sequence[WorkerSlot] = (),
) -> list[str]:
    by_name = {slot.name: slot for slot in slots}
    claims = sorted(repository.claims, key=lambda claim: claim.issue)
    held = [claim for claim in claims if claim.state.holds]
    mine = [claim for claim in held if claim.holder.manager == manager_id]
    others = [claim for claim in held if claim.holder.manager != manager_id]
    finished = len(claims) - len(held)
    lines = [repository.repository]
    if mine:
        lines += _claims(
            "Claims held by this manager",
            "WORKER",
            mine,
            lambda claim: claim.holder.worker,
            policy,
            now,
            model=lambda claim: model_of(by_name.get(claim.holder.worker)),
        )
    if others:
        lines += _claims(
            "Claims held by other managers",
            "HOLDER",
            others,
            lambda claim: str(claim.holder),
            policy,
            now,
        )
    if not held:
        lines.append("  no active claims")
    if finished:
        lines.append(f"  {finished} finished (done/released)")
    return lines


def _accounts(usage: Sequence[AccountUsage], now: datetime) -> list[str]:
    """Each account's state, its reset while limited and the issues paused on it (F13.7)."""
    lines = ["Accounts"]
    if not usage:
        return [*lines, "  no accounts configured"]
    rows = [["ACCOUNT", "STATE", "RESETS", "WAITING"]]
    for account in usage:
        rows.append(
            [
                account.name,
                account.state,
                when(account.limited_until, now) if account.limited_until else "-",
                ", ".join(f"{repo}#{issue}" for repo, issue in account.waiting) or "-",
            ]
        )
    return lines + table(rows, "  ")


def render_status(
    manager_id: str,
    slots: Sequence[WorkerSlot],
    limited_until: Callable[[str], datetime | None],
    repositories: Sequence[RepositoryClaims],
    policy: ClaimPolicy,
    now: datetime,
    accounts: Sequence[str] = (),
) -> str:
    """The whole ``status`` output, one section per block, blocks separated by a blank line."""
    sections = [["Manager", f"  {manager_id}"]]
    workers = ["Workers"]
    if slots:
        rows = [["NAME", "AGENT", "ACCOUNT", "MODEL", "STATE"]]
        for slot in slots:
            state = _worker_state(slot, limited_until(slot.account), now)
            rows.append([slot.name, slot.agent, slot.account, model_of(slot), state])
        workers += table(rows, "  ")
    else:
        workers.append("  no workers configured")
    sections.append(workers)
    paused = (
        (repository.repository, claim.issue, claim.holder.worker)
        for repository in repositories
        for claim in repository.claims
        if claim.state is ClaimState.PAUSED and claim.holder.manager == manager_id
    )
    account_of = {slot.name: slot.account for slot in slots}
    sections.append(_accounts(account_usage(accounts, account_of, limited_until, paused, now), now))
    for repository in repositories:
        sections.append(_repository(repository, manager_id, policy, now, slots))
    if not repositories:
        sections.append(
            ["no repositories watched; add one with 'github-issue-worker add <owner/repo>'"]
        )
    return "\n\n".join("\n".join(lines) for lines in sections) + "\n"
