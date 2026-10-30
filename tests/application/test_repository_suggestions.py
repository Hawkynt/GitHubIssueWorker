from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from github_issue_worker.application.repository_suggestions import (
    PushableRepositories,
    Suggestions,
    pushable_repositories,
    suggest,
)
from github_issue_worker.ports.github import GhUnavailable, GitHubError, RateLimited, Response

CACHE = ("Hawkynt/ShiftDiff", "Hawkynt/MediaUploadService", "HawkyntOrg/Tool")


def test_typing_part_of_an_owner_suggests_the_owners_and_no_repositories() -> None:
    assert suggest(CACHE, (), "Ha") == Suggestions("owner", ("Hawkynt", "HawkyntOrg"))


def test_typing_after_the_slash_suggests_that_owners_repositories() -> None:
    assert suggest(CACHE, (), "Hawkynt/Sh") == Suggestions("repository", ("Hawkynt/ShiftDiff",))


def test_watched_repositories_are_not_suggested() -> None:
    found = suggest(CACHE, ("Hawkynt/ShiftDiff",), "Hawkynt/")
    assert found == Suggestions("repository", ("Hawkynt/MediaUploadService",))


def test_a_repository_the_account_cannot_push_to_is_not_suggested() -> None:
    # pushable_repositories() leaves other/lib out, so the cache never holds it
    assert suggest(CACHE, (), "other/").items == ()


def test_matching_ignores_case_and_sorts_alphabetically() -> None:
    assert suggest(("Bob/x", "b/zeta", "a/x"), (), "B").items == ("b", "Bob")
    assert suggest(("Bob/x", "bob/y"), (), "bo").items == ("Bob",)  # one owner, however spelt
    assert suggest(("o/Zeta", "o/alpha"), (), "O/").items == ("o/alpha", "o/Zeta")
    assert suggest(CACHE, ("hawkynt/shiftdiff",), "hawkynt/s").items == ()


def test_an_owner_whose_repositories_are_all_watched_is_not_suggested() -> None:
    assert suggest(CACHE, ("HawkyntOrg/Tool",), "Ha").items == ("Hawkynt",)


def test_empty_text_and_urls_suggest_nothing() -> None:
    assert suggest(CACHE, (), "").items == ()
    assert suggest(CACHE, (), "https://github.com/Ha").items == ()
    assert suggest(CACHE, (), "Hawkynt/ShiftDiff/issues").items == ()


def test_suggestions_are_capped() -> None:
    names = tuple(f"o/r{index:03}" for index in range(50))
    assert len(suggest(names, (), "o/", limit=20).items) == 20


class PagedApi:
    def __init__(self, pages: dict[str, Response]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        self.calls.append((path, params))
        return self.pages[path]


def repo(name: str, push: bool) -> dict[str, Any]:
    return {"full_name": name, "permissions": {"pull": True, "push": push}}


def test_the_list_is_every_page_of_user_repos_with_push_access() -> None:
    api = PagedApi(
        {
            "user/repos": Response(
                200,
                {"link": '<https://api.github.com/user/repos?page=2&per_page=100>; rel="next"'},
                [repo("Hawkynt/ShiftDiff", True), repo("other/lib", False)],
            ),
            "user/repos?page=2&per_page=100": Response(200, {}, [repo("HawkyntOrg/Tool", True)]),
        }
    )
    assert pushable_repositories(api) == ("Hawkynt/ShiftDiff", "HawkyntOrg/Tool")
    assert api.calls[0] == ("user/repos", {"per_page": "100"})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Deferred:
    """Holds the background refreshes instead of starting threads, so a test runs them."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def __call__(self, work: Callable[[], None]) -> None:
        self.pending.append(work)

    def run(self) -> None:
        while self.pending:
            self.pending.pop(0)()


class Fetch:
    def __init__(self, *results: tuple[str, ...] | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> tuple[str, ...]:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def cache(fetch: Fetch) -> tuple[PushableRepositories, Clock, Deferred]:
    clock, spawn = Clock(), Deferred()
    return PushableRepositories(fetch, monotonic=clock, spawn=spawn), clock, spawn


def test_before_the_first_read_finishes_nothing_is_served_and_nothing_waits() -> None:
    fetch = Fetch(CACHE)
    pushable, _, spawn = cache(fetch)
    pushable.refresh()
    assert pushable.names() == ()
    assert fetch.calls == 0  # still pending: names() did not wait for it
    spawn.run()
    assert pushable.names() == CACHE


def test_a_fresh_list_is_served_without_asking_github_again() -> None:
    fetch = Fetch(CACHE)
    pushable, clock, spawn = cache(fetch)
    pushable.refresh()
    spawn.run()
    for _ in range(5):
        clock.now += 60
        assert pushable.names() == CACHE
    spawn.run()
    assert fetch.calls == 1


def test_a_stale_list_starts_one_background_refresh_and_is_served_meanwhile() -> None:
    fetch = Fetch(CACHE, ("Hawkynt/New",))
    pushable, clock, spawn = cache(fetch)
    pushable.refresh()
    spawn.run()
    clock.now += 601
    assert pushable.names() == CACHE
    assert pushable.names() == CACHE
    assert len(spawn.pending) == 1
    spawn.run()
    assert pushable.names() == ("Hawkynt/New",)
    assert fetch.calls == 2


@pytest.mark.parametrize(
    "failure",
    [
        GitHubError(500, "boom"),
        RateLimited(403, "rate limit", __import__("datetime").datetime(2026, 10, 30)),
        GhUnavailable("gh not logged in"),
    ],
)
def test_a_failed_read_keeps_the_previous_list_and_is_logged(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    fetch = Fetch(CACHE, failure, ("Hawkynt/New",))
    pushable, clock, spawn = cache(fetch)
    pushable.refresh()
    spawn.run()
    clock.now += 601
    with caplog.at_level(logging.WARNING):
        pushable.names()
        spawn.run()
    assert pushable.names() == CACHE
    assert "could not read the repositories" in caplog.text
    clock.now += 30
    pushable.names()
    assert spawn.pending == []  # no retry storm right after a failure
    clock.now += 31
    pushable.names()
    spawn.run()
    assert pushable.names() == ("Hawkynt/New",)


def test_a_failed_first_read_serves_nothing() -> None:
    pushable, _, spawn = cache(Fetch(GhUnavailable("no gh")))
    pushable.refresh()
    spawn.run()
    assert pushable.names() == ()
