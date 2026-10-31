"""Conditional polling of one repository's issues and comments (F2.1, F2.5).

Each stream keeps its own ``since`` cursor and ETag. An unchanged repository answers ``304`` to both
requests and costs no rate limit. The cursor is the newest ``updated_at`` seen, so the newest item
comes back once more on the next poll; consumers are idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, TypeVar

from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.ports.clock import Clock
from github_issue_worker.ports.github import GitHubApi, next_link

_EPOCH = datetime.min.replace(tzinfo=UTC)
T = TypeVar("T", Issue, Comment)


@dataclass(frozen=True, slots=True)
class PollState:
    """What one repository's poller remembers between polls."""

    watched_since: datetime | None = None
    issues_since: datetime | None = None
    comments_since: datetime | None = None
    issues_etag: str | None = None
    comments_etag: str | None = None
    last_polled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PollResult:
    issues: tuple[Issue, ...] = ()
    comments: tuple[Comment, ...] = ()


def _time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _actor(user: dict[str, Any] | None) -> Actor:
    user = user or {}
    return Actor(login=user.get("login", "ghost"), is_bot=user.get("type") == "Bot")


def parse_issue(item: dict[str, Any]) -> Issue:
    return Issue(
        number=int(item["number"]),
        title=item.get("title") or "",
        body=item.get("body") or "",
        state="closed" if item.get("state") == "closed" else "open",
        author=_actor(item.get("user")),
        labels=frozenset(label["name"] for label in item.get("labels", [])),
        is_pull_request="pull_request" in item,
        created_at=_time(item["created_at"]),
        updated_at=_time(item["updated_at"]),
        state_reason=item.get("state_reason"),
    )


def parse_comment(item: dict[str, Any]) -> Comment:
    return Comment(
        id=int(item["id"]),
        issue=int(item["issue_url"].rstrip("/").rsplit("/", 1)[1]),
        author=_actor(item.get("user")),
        body=item.get("body") or "",
        created_at=_time(item["created_at"]),
        updated_at=_time(item["updated_at"]),
    )


@dataclass(frozen=True, slots=True)
class Poller:
    github: GitHubApi
    clock: Clock

    def poll(
        self, repository: str, state: PollState, process_backlog: bool
    ) -> tuple[PollResult, PollState]:
        if state.watched_since is None:
            if not process_backlog:
                now = self.clock.now()
                return PollResult(), PollState(
                    watched_since=now, issues_since=now, comments_since=now
                )
            state = replace(state, watched_since=_EPOCH)

        issues, issues_since, issues_etag = self._stream(
            f"repos/{repository}/issues",
            {"state": "all"},
            state.issues_since,
            state.issues_etag,
            parse_issue,
        )
        comments, comments_since, comments_etag = self._stream(
            f"repos/{repository}/issues/comments",
            {},
            state.comments_since,
            state.comments_etag,
            parse_comment,
        )
        new_state = replace(
            state,
            issues_since=issues_since,
            comments_since=comments_since,
            issues_etag=issues_etag,
            comments_etag=comments_etag,
            last_polled_at=self.clock.now(),
        )
        return PollResult(issues=issues, comments=comments), new_state

    def _stream(
        self,
        path: str,
        params: dict[str, str],
        since: datetime | None,
        etag: str | None,
        parse: Callable[[dict[str, Any]], T],
    ) -> tuple[tuple[T, ...], datetime | None, str | None]:
        query = {**params, "sort": "updated", "direction": "asc", "per_page": "100"}
        if since is not None:
            query["since"] = _stamp(since)
        headers = {"If-None-Match": etag} if etag else {}

        response = self.github.request("GET", path, params=query, headers=headers)
        if response.status == 304:
            return (), since, etag

        new_etag = response.headers.get("etag", etag)
        items = [parse(item) for item in response.body or []]
        page = next_link(response.headers)
        while page is not None:
            following = self.github.request("GET", page)
            items += [parse(item) for item in following.body or []]
            page = next_link(following.headers)

        newest = max((item.updated_at for item in items), default=since)
        return tuple(items), newest, new_etag
