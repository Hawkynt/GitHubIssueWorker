from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from github_issue_worker.application.history import RunRecord
from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.ports.github import Response
from github_issue_worker.ui.live import RuntimeBackend, issue_state, worker_model


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


def test_the_live_backend_reads_the_pushable_repositories_in_the_background() -> None:
    # F14.16: the read starts with the UI and nothing waits for it.
    release = threading.Event()

    class SlowApi:
        def request(self, method: str, path: str, **_: object) -> Response:
            release.wait(5)
            return Response(
                200,
                {},
                [
                    {"full_name": "Hawkynt/ShiftDiff", "permissions": {"push": True}},
                    {"full_name": "other/lib", "permissions": {"push": False}},
                ],
            )

    runtime = SimpleNamespace(api=SlowApi())
    backend = RuntimeBackend(runtime, SimpleNamespace(), Path("config.json"), lambda _: [])  # type: ignore[arg-type]
    assert backend.pushable_repositories() == ()
    release.set()
    deadline = time.monotonic() + 2
    while not backend.pushable_repositories() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert backend.pushable_repositories() == ("Hawkynt/ShiftDiff",)


def test_the_live_backend_carries_runtime_worker_priority_into_the_flight_log() -> None:
    now = datetime(2026, 10, 30, 15, tzinfo=UTC)
    slots = [
        WorkerSlot(
            "fallback",
            "codex",
            "plus",
            priority=9,
            busy=True,
            current=("o/a", 1),
            started_at=now,
        ),
        WorkerSlot("preferred", "claude", "max", priority=1),
    ]
    runtime = SimpleNamespace(
        api=SimpleNamespace(clock=SimpleNamespace(now=lambda: now), request=lambda *a, **k: None),
        config=SimpleNamespace(workers=[], repositories=[]),
        state=SimpleNamespace(limited_until=lambda _: None),
        workspace=SimpleNamespace(worktree_path=lambda r, i: Path("/tmp/w")),
        watches={},
        manager_id="m-1",
        history=SimpleNamespace(load=lambda: []),
    )
    backend = RuntimeBackend(
        runtime,  # type: ignore[arg-type]
        SimpleNamespace(workers=slots),  # type: ignore[arg-type]
        Path("config.json"),
        lambda _: [],
    )
    snap = backend.snapshot()
    assert [w.priority for w in snap.workers] == [9, 1]
    slots[0].priority = 1
    slots[1].priority = 5
    assert [w.priority for w in backend.snapshot().workers] == [1, 5]
