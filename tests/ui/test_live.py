from __future__ import annotations

import threading
import time
from dataclasses import replace
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
    backend._fleet = []
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


# --- the Issue page over the running manager (F14.6) ---------------------------------------------


def _issue_backend(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A live backend over one watched repository with an open, claimed issue 42."""
    from datetime import timedelta

    from github_issue_worker.domain.claim import ClaimPolicy, Holder, RunEntry
    from tests.application.run_fakes import FakeHost
    from tests.application.test_issue_run import ISSUE, T0
    from tests.fakes import FakeClock, InMemoryClaimStore

    policy = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
    store = InMemoryClaimStore()
    holder = Holder("m", "w")
    first = policy.new_claim(42, holder, T0, "issue/42-x")
    shared = RunEntry("other", "codex", "codex", "GPT 6.1 Sol", "first", "needs-input", T0, T0)
    version = store.create(first)
    for hours in (1, 2):
        version = store.replace(version, policy.renewed(first, T0 + timedelta(hours=hours)))
    current = store.read(42)
    assert current is not None
    store.replace(version, policy.renewed(current.claim, T0 + timedelta(hours=3), step="implement"))
    current = store.read(42)
    assert current is not None
    store.replace(current.version, current.claim.replace(runs=(shared,)))
    host = FakeHost(repository="o/r", issues={42: replace(ISSUE, number=42)})
    worktree = tmp_path / "issues" / "42"
    local = RunRecord(
        "o/r",
        42,
        "w",
        "claude",
        "Claude Opus 5.5",
        "continue",
        "failed",
        T0 + timedelta(hours=4),
        T0 + timedelta(hours=5),
        T0,
        "m",
    )
    runtime = SimpleNamespace(
        manager_id="m",
        api=SimpleNamespace(clock=FakeClock(T0 + timedelta(hours=6))),
        watches={"o/r": SimpleNamespace(store=store, host=host)},
        workspace=SimpleNamespace(worktree_path=lambda repository, issue: worktree),
        history=SimpleNamespace(load=lambda: [local]),
        config=SimpleNamespace(repositories=[]),
    )
    slot = WorkerSlot("w", "claude", "a")
    backend = object.__new__(RuntimeBackend)
    backend._runtime = runtime
    backend._manager = SimpleNamespace(workers=[slot])
    backend._history = []
    backend._history_at = 0.0
    backend._claims_at = time.monotonic()
    backend._issue_cache = {}
    stored = store.read(42)
    assert stored is not None
    from github_issue_worker.ui.model import ClaimView

    view = ClaimView(
        "o/r",
        42,
        "m/w",
        True,
        "working",
        "implement",
        stored.claim.renewed_at,
        stored.claim.expires_at,
        "issue/42-x",
    )
    backend._claims = (view,)
    backend._checkpoints = {("o/r", 42): view}
    backend._open = {"o/r": host.open_issues()}
    backend._fleet = []
    from github_issue_worker.application.history import fleet_records

    backend._fleet = fleet_records("o/r", [stored.claim])
    return backend, host, slot, worktree, local


def test_the_live_issue_detail_merges_the_claims_runs_with_the_local_history(
    tmp_path: Path,
) -> None:
    backend, _host, _slot, _worktree, local = _issue_backend(tmp_path)
    detail = backend.issue("o/r", 42)
    assert detail is not None
    assert [(run.manager, run.verdict) for run in detail.runs] == [
        ("other", "needs-input"),
        ("m", "failed"),
    ]
    assert detail.runs[-1] == local


def test_the_live_issue_detail_reads_journal_and_outcome_from_the_worktree(
    tmp_path: Path,
) -> None:
    backend, _host, _slot, worktree, _local = _issue_backend(tmp_path)
    detail = backend.issue("o/r", 42)
    assert detail is not None and detail.journal is None and detail.outcome is None
    (worktree / ".ghiw").mkdir(parents=True)
    (worktree / ".ghiw" / "journal.md").write_text("# Journal\n<b>next</b>", encoding="utf-8")
    (worktree / ".ghiw" / "outcome.json").write_text("{not json", encoding="utf-8")
    detail = backend.issue("o/r", 42)
    assert detail is not None
    assert detail.journal == "# Journal\n<b>next</b>" and detail.outcome is None
    (worktree / ".ghiw" / "outcome.json").write_text(
        '{"outcome": "done", "summary": "Retries back off.", "scenarios": ["a"]}',
        encoding="utf-8",
    )
    detail = backend.issue("o/r", 42)
    assert detail is not None and detail.outcome is not None
    assert detail.outcome.summary == "Retries back off."


def test_the_live_issue_detail_tails_the_running_workers_log(tmp_path: Path) -> None:
    backend, _host, slot, _worktree, _local = _issue_backend(tmp_path)
    detail = backend.issue("o/r", 42)
    assert detail is not None and detail.log is None and detail.log_worker is None
    slot.busy = True
    slot.current = ("o/r", 42)
    detail = backend.issue("o/r", 42)
    assert detail is not None and detail.log is None and detail.log_worker == "w"
    slot.log_path = tmp_path / "run.log"
    slot.log_path.write_text("\n".join(f"line {n}" for n in range(1, 301)), encoding="utf-8")
    detail = backend.issue("o/r", 42)
    assert detail is not None and detail.log_worker == "w" and detail.log is not None
    lines = detail.log.splitlines()
    assert len(lines) == 200 and lines[0] == "line 101" and lines[-1] == "line 300"


def test_the_live_issue_detail_carries_the_folded_claim_history(tmp_path: Path) -> None:
    backend, _host, _slot, _worktree, _local = _issue_backend(tmp_path)
    detail = backend.issue("o/r", 42)
    assert detail is not None
    assert [(row.what, row.renewals) for row in detail.history] == [
        ("run recorded", 0),
        ("step", 0),
        ("renewed", 2),
        ("claimed", 0),
    ]


def test_an_unwatched_or_unknown_issue_has_no_detail(tmp_path: Path) -> None:
    backend, _host, _slot, _worktree, _local = _issue_backend(tmp_path)
    assert backend.issue("o/other", 42) is None
    assert backend.issue("o/r", 7) is None


def test_skipping_labels_the_issue_through_its_repositorys_host(tmp_path: Path) -> None:
    backend, host, _slot, _worktree, _local = _issue_backend(tmp_path)
    backend.skip("o/r", 42)
    assert host.added_labels == {42: ["worker:skip"]}


def test_a_skip_github_refuses_is_reported(tmp_path: Path) -> None:
    from github_issue_worker.ports.github import GitHubError
    from github_issue_worker.ui.model import ActionError

    backend, host, _slot, _worktree, _local = _issue_backend(tmp_path)

    def refuse(issue: int, labels: list[str]) -> None:
        raise GitHubError(403, "Resource not accessible")

    host.add_labels = refuse  # type: ignore[method-assign]
    with pytest.raises(ActionError, match="Resource not accessible"):
        backend.skip("o/r", 42)
    with pytest.raises(ActionError, match="not watched"):
        backend.skip("o/other", 42)


def test_the_pull_request_is_found_by_the_claims_branch(tmp_path: Path) -> None:
    from github_issue_worker.ports.repository import PullRequest

    backend, host, _slot, _worktree, _local = _issue_backend(tmp_path)
    assert backend.pull_request("o/r", 42) is None
    host.pulls[7] = PullRequest(7, "sha", "https://github.com/o/r/pull/7", "clean")
    host.pull_bodies[7] = ("issue/42-x", "")
    host.head_of["issue/42-x"] = "sha"
    assert backend.pull_request("o/r", 42) == "https://github.com/o/r/pull/7"
    assert backend.pull_request("o/r", 9) is None
