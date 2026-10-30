"""Agent adapters, one per agent kind, plus what they share."""

from __future__ import annotations

import json
from pathlib import Path

from github_issue_worker.domain.outcome import OUTCOME_SCHEMA, Outcome, OutcomeError
from github_issue_worker.ports.agents import OUTCOME_FILE


def prepare_outcome(worktree: Path) -> tuple[Path, Path]:
    """Remove a previous run's outcome and write the schema. Returns (outcome, schema) paths."""
    outcome = worktree / OUTCOME_FILE
    outcome.parent.mkdir(parents=True, exist_ok=True)
    outcome.unlink(missing_ok=True)
    schema = outcome.parent / "outcome.schema.json"
    schema.write_text(json.dumps(OUTCOME_SCHEMA, indent=2) + "\n", encoding="utf-8")
    return outcome, schema


def read_outcome(worktree: Path) -> tuple[Outcome | None, str | None]:
    """The run's outcome, or None with the reason it is unusable."""
    path = worktree / OUTCOME_FILE
    if not path.exists():
        return None, f"the run wrote no outcome ({OUTCOME_FILE})"
    try:
        return Outcome.parse(path.read_text(encoding="utf-8")), None
    except OutcomeError as error:
        return None, str(error)
