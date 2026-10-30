from __future__ import annotations

from datetime import UTC, datetime

import pytest

from github_issue_worker.application.history import RunRecord
from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.ui.live import issue_state, worker_model


@pytest.mark.parametrize(
    ("labels", "claim_state", "local", "expected"),
    [
        (set(), "working", True, "worked here"),
        (set(), "working", False, "worked elsewhere"),
        (set(), "paused", False, "paused until the usage limit resets"),
        ({"worker:queued"}, None, False, "waiting for a worker"),
        ({"worker:needs-input"}, None, False, "waiting for your answer"),
        ({"worker:awaiting-review"}, None, False, "waiting for review or CI"),
        ({"worker:failed"}, None, False, "failed; its last comment says why"),
        ({"worker:unserved"}, None, False, "no worker can serve it"),
        ({"worker:skip", "bug"}, None, False, "skipped (worker:skip)"),
        ({"bug"}, None, False, "not picked up"),
    ],
)
def test_an_issue_says_what_is_happening(
    labels: set[str], claim_state: str | None, local: bool, expected: str
) -> None:
    assert issue_state(frozenset(labels), claim_state, local) == expected


def test_an_idle_worker_shows_the_model_of_its_last_run() -> None:
    finished = datetime(2026, 10, 30, 15, tzinfo=UTC)
    runs = [
        RunRecord(
            "o/a",
            1,
            "local",
            "local",
            "Qwen3.8 Flash Next IQ3_S",
            "first",
            "merged",
            finished,
            finished,
            finished,
        ),
    ]
    assert worker_model(WorkerSlot("local", "claude", "gpu"), runs) == "Qwen3.8 Flash Next IQ3_S"
    assert worker_model(WorkerSlot("codex", "codex", "plus"), runs) == "-"
    observed = WorkerSlot("local", "claude", "gpu", observed_model="claude-opus-5-5")
    assert worker_model(observed, runs) == "Claude Opus 5.5"
