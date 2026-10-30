"""The agent adapter contract (F12.1): how to start an agent, and what its output means.

An adapter is split in two pure halves so it can be tested against captured vendor output without
running anything: `invocation` says what to start, `report` reads what came back.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from github_issue_worker.domain.outcome import Outcome

OUTCOME_FILE = Path(".ghiw") / "outcome.json"
JOURNAL_FILE = Path(".ghiw") / "journal.md"


@dataclass(frozen=True, slots=True)
class AgentRun:
    worktree: Path
    prompt: str
    session: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Invocation:
    argv: list[str]
    cwd: Path
    stdin: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    """Variables to set on top of the scrubbed environment."""


@dataclass(frozen=True, slots=True)
class UsageLimit:
    message: str
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RunReport:
    session: str | None
    outcome: Outcome | None
    limit: UsageLimit | None = None
    usage: TokenUsage | None = None
    error: str | None = None


class AgentAdapter(Protocol):
    kind: str

    def invocation(self, run: AgentRun) -> Invocation:
        """What to start for ``run``. May prepare files under the worktree's ``.ghiw``."""
        ...

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        """What the finished process means."""
        ...
