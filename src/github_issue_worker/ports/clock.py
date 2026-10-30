"""The time claims are judged by: GitHub's, never the local machine's (F11.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware."""
        ...
