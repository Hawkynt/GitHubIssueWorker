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
        ({"worker:split"}, None, False, "split; waiting for its sub-issues"),
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


def test_resource_paused_worker_keeps_its_issue_reason_and_conflict_in_the_live_ui() -> None:
    from datetime import timedelta

    from github_issue_worker.configuration import Configuration
    from github_issue_worker.ui.model import ClaimView
    from tests.fakes import FakeClock

    now = datetime(2026, 10, 30, tzinfo=UTC)
    reset = now + timedelta(hours=2)
    runtime = SimpleNamespace(
        manager_id="m",
        config=Configuration(),
        state=SimpleNamespace(limited_until=lambda account: reset),
        workspace=SimpleNamespace(worktree_path=lambda repo, issue: Path("/work/42")),
    )
    slot = WorkerSlot("w", "codex", "a")
    # Isolate the read-only mapping; background I/O is covered separately.
    backend = object.__new__(RuntimeBackend)
    backend._runtime = runtime
    backend._history_at = time.monotonic()
    backend._history = []
    backend._claims = (
        ClaimView(
            "o/r",
            42,
            "m/w",
            True,
            "paused",
            "implement",
            now,
            reset,
            "issue/42",
            reset,
            True,
            "Rate control",
        ),
    )
    view = backend._worker(slot, FakeClock(now).now())
    assert view.state == "limited" and view.issue == 42
    assert view.merge_conflict and view.reason == "Rate control" and view.limited_until == reset
    slot.busy = True
    slot.current = ("o/r", 42)
    slot.merge_conflict = False
    assert backend._worker(slot, now).state == "working"
    assert not backend._worker(slot, now).merge_conflict


def test_live_issue_shows_checkpoint_conflict_even_after_claim_release() -> None:
    from github_issue_worker.ui.model import ClaimView
    from tests.application.test_issue_run import ISSUE, T0

    backend = object.__new__(RuntimeBackend)
    backend._runtime = SimpleNamespace(manager_id="m")
    backend._manager = SimpleNamespace(workers=[])
    backend._claims = ()
    backend._checkpoints = {
        ("o/r", 42): ClaimView(
            "o/r", 42, "m/w", True, "released", "failed", T0, T0, "issue/42", merge_conflict=True
        )
    }
    backend._open = {"o/r": [ISSUE]}
    (view,) = backend._issues(T0)
    assert view.worker is None and view.merge_conflict


def test_resumed_local_work_clears_wait_details_before_the_claim_cache_refreshes() -> None:
    from github_issue_worker.ui.model import ClaimView
    from tests.application.test_issue_run import ISSUE, T0

    backend = object.__new__(RuntimeBackend)
    backend._runtime = SimpleNamespace(manager_id="m")
    backend._manager = SimpleNamespace(
        workers=[WorkerSlot("w", "codex", "a", busy=True, current=("o/r", 42))]
    )
    waiting = ClaimView(
        "o/r", 42, "m/w", True, "paused", "implement", T0, T0, "issue/42", T0, True, "Rate control"
    )
    backend._claims = (waiting,)
    backend._checkpoints = {("o/r", 42): waiting}
    backend._open = {"o/r": [ISSUE]}
    (view,) = backend._issues(T0)
    assert view.state == "worked here"
    assert view.wait_reason == "" and view.paused_until is None
    assert not view.merge_conflict
