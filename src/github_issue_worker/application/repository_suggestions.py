"""Completing the add box: owners, then repositories the account can push to (F14.16)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from typing import Literal

from github_issue_worker.ports.github import GitHubApi, next_link

log = logging.getLogger(__name__)

TIME_TO_LIVE = 600.0
"""Seconds a read list is served before it is read again."""
RETRY = 60.0
"""Seconds after a failed read before the next one."""
LIMIT = 20


@dataclass(frozen=True, slots=True)
class Suggestions:
    kind: Literal["owner", "repository"]
    """Owners are completed to ``owner/``; repositories are ``owner/name``."""
    items: tuple[str, ...] = ()


def pushable_repositories(api: GitHubApi) -> tuple[str, ...]:
    """Every repository the authenticated account can push to, as ``owner/name``."""
    found: list[str] = []
    response = api.request("GET", "user/repos", params={"per_page": "100"})
    while True:
        found += [
            item["full_name"]
            for item in response.body or []
            if (item.get("permissions") or {}).get("push")
        ]
        page = next_link(response.headers)
        if page is None:
            return tuple(found)
        response = api.request("GET", page)


def suggest(
    names: Iterable[str], watched: Collection[str], typed: str, limit: int = LIMIT
) -> Suggestions:
    """What ``typed`` completes to: owners before the ``/``, that owner's repositories after it.

    Matching is a case-insensitive prefix; watched repositories, and owners left with nothing
    else, are not suggested.
    """
    seen = {name.casefold() for name in watched}
    candidates = [name for name in names if name.casefold() not in seen]
    owner, slash, rest = typed.casefold().partition("/")
    if not slash:
        if not owner:
            return Suggestions("owner")
        owners: dict[str, str] = {}
        for name in candidates:
            spelt = name.partition("/")[0]
            if spelt.casefold().startswith(owner):
                owners.setdefault(spelt.casefold(), spelt)
        return Suggestions("owner", tuple(sorted(owners.values(), key=str.casefold)[:limit]))
    if "/" in rest:
        return Suggestions("repository")
    matching = [
        name
        for name in candidates
        if name.partition("/")[0].casefold() == owner
        and name.partition("/")[2].casefold().startswith(rest)
    ]
    return Suggestions("repository", tuple(sorted(matching, key=str.casefold)[:limit]))


def _thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class PushableRepositories:
    """The pushable repositories, read in the background and kept in memory for a while.

    Reading never waits on GitHub: until the first read finishes the list is empty, and a stale
    list is served while one background read replaces it.
    """

    def __init__(
        self,
        fetch: Callable[[], tuple[str, ...]],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        spawn: Callable[[Callable[[], None]], None] = _thread,
        time_to_live: float = TIME_TO_LIVE,
        retry: float = RETRY,
    ) -> None:
        self._fetch = fetch
        self._monotonic = monotonic
        self._spawn = spawn
        self._time_to_live = time_to_live
        self._retry = retry
        self._names: tuple[str, ...] = ()
        self._next_read: float | None = None
        self._reading = threading.Lock()

    def names(self) -> tuple[str, ...]:
        if self._next_read is None or self._monotonic() >= self._next_read:
            self.refresh()
        return self._names

    def refresh(self) -> None:
        """Start one background read, unless one is already under way."""
        if not self._reading.acquire(blocking=False):
            return

        def read() -> None:
            try:
                self._names = self._fetch()
                self._next_read = self._monotonic() + self._time_to_live
            except Exception as error:
                log.warning("could not read the repositories you can push to: %s", error)
                self._next_read = self._monotonic() + self._retry
            finally:
                self._reading.release()

        self._next_read = self._monotonic() + self._time_to_live
        self._spawn(read)
