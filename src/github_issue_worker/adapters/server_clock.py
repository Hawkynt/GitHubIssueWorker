"""GitHub's time, learned from the ``Date`` header of its responses (F11.2).

Claims are judged against this clock so that managers whose local clocks disagree still agree on
which claims have expired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from github_issue_worker.ports.clock import Clock


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ServerClock:
    """The local clock corrected by the offset to the server seen in the latest response."""

    def __init__(self, local: Clock | None = None) -> None:
        self._local = local or _SystemClock()
        self._offset = timedelta()

    def observe(self, date_header: str) -> None:
        """Record the server's ``Date`` header; unparsable values are ignored."""
        try:
            server = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            return
        if server.tzinfo is None:
            return
        self._offset = server - self._local.now()

    def now(self) -> datetime:
        return self._local.now() + self._offset
