"""The usage state of each account (F13.7): limited or available, its reset, the issues it holds up.

Pure: the caller gathers the accounts, which instance bills against which, the recorded limits and
this manager's paused claims; `status` and the UI show the same answer from the same inputs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AccountState = Literal["available", "limited"]


@dataclass(frozen=True, slots=True)
class AccountUsage:
    """One account as `status` and the UI show it."""

    name: str
    limited_until: datetime | None = None
    """The reset of a limit that still holds; ``None`` once it has passed or when there is none."""
    waiting: tuple[tuple[str, int], ...] = ()
    """Issues (repository, number) this manager's workers on the account paused for its limit."""

    @property
    def state(self) -> AccountState:
        return "limited" if self.limited_until else "available"


def account_usage(
    accounts: Iterable[str],
    account_of: Mapping[str, str],
    limited_until: Callable[[str], datetime | None],
    paused: Iterable[tuple[str, int, str]],
    now: datetime,
) -> tuple[AccountUsage, ...]:
    """Every account, configured ones first, then any other an instance bills against.

    ``account_of`` maps an instance name (``claude``, ``claude.2``) to its account; ``paused`` holds
    (repository, issue, instance) for each paused claim held by this manager.
    """
    names = list(dict.fromkeys([*accounts, *account_of.values()]))
    waiting: dict[str, list[tuple[str, int]]] = {}
    for repository, issue, instance in paused:
        account = account_of.get(instance)
        if account is not None:
            waiting.setdefault(account, []).append((repository, issue))
    usage = []
    for name in names:
        until = limited_until(name)
        usage.append(
            AccountUsage(
                name,
                until if until and until > now else None,
                tuple(sorted(waiting.get(name, ()))),
            )
        )
    return tuple(usage)
