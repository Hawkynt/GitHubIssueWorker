"""The GitHub API as the application sees it: requests in, status, headers and JSON out."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Response:
    """A GitHub response. Header names are lower case; ``body`` is decoded JSON or None."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None


class GitHubError(Exception):
    """GitHub answered with an error status."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


class RateLimited(GitHubError):
    """A primary or secondary rate limit; nothing should be sent before ``until`` (server time)."""

    def __init__(self, status: int, message: str, until: datetime) -> None:
        self.until = until
        super().__init__(status, message)


class GhUnavailable(Exception):
    """``gh`` produced no HTTP response at all: not installed, not logged in, or no network."""


class GitHubApi(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        """Send one request. 2xx and 304 return; other statuses raise `GitHubError`."""
        ...
