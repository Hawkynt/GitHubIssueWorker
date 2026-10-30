"""F14.18: blocked work has a colour and a readable reason."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import MANAGER, NOW, DemoBackend
from github_issue_worker.ui.model import ClaimView, WorkerState
from github_issue_worker.ui.pages import _agent_row, _remote_row, _track, issues_fragment


def test_feedback_wait_is_yellow_after_claim_release() -> None:
    backend = DemoBackend()
    waiting = next(i for i in backend.snapshot().issues if i.state == "waiting for your answer")
    assert waiting.worker is None
    html = issues_fragment(replace(backend.snapshot(), issues=(waiting,), claims=()))
    assert '<span class="flag feedback">waiting for your answer</span>' in html


@pytest.mark.parametrize("state", ["idle", "paused", "limited"])
def test_waiting_agent_box_is_gray_and_active_agent_stays_active(state: WorkerState) -> None:
    worker = replace(DemoBackend().snapshot().workers[0], state=state, limited_until=None)
    html = _agent_row((worker,), NOW, {}, {})
    assert 'class="lane waiting"' in html
    if state == "limited":
        assert '<span class="flag limited">Usage limit</span>' in html
    active = _agent_row((replace(worker, state="working"),), NOW, {}, {})
    assert 'class="lane waiting"' not in active


@pytest.mark.parametrize("state", ["limited", "paused"])
def test_resource_wait_chip_preserves_reason_reset_and_conflict(state: WorkerState) -> None:
    worker = replace(
        DemoBackend().snapshot().workers[0],
        state=state,
        reason="Spending allowance reached",
        limited_until=NOW,
        merge_conflict=True,
    )
    html = _agent_row((worker,), NOW, {}, {})
    assert f'class="flag {state}"' in html
    assert "Spending allowance reached" in html and "resets" in html
    assert 'class="conflict"' in html and "Merge conflict" in html


def test_merge_chip_stays_red_during_repairs_and_clears_on_resolution() -> None:
    for step in ("implement", "verify", "checks", "merge"):
        track = _track(step, merge_conflict=True)
        assert ">merge</span>" in track and 'title="Merge conflict"' in track
        assert 'class="conflict"' in track or 'class="now conflict"' in track
    assert "conflict" not in _track("merge", merge_conflict=False)


def test_live_refresh_clears_feedback_and_resource_wait_colors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = DemoBackend()
    snapshot = backend.snapshot()
    waiting = next(i for i in snapshot.issues if i.state == "waiting for your answer")
    shown = replace(snapshot, issues=(waiting,))
    monkeypatch.setattr(backend, "snapshot", lambda: shown)
    client = TestClient(create_app(backend))
    assert 'class="flag feedback"' in client.get("/?fragment=1").text
    shown = replace(
        snapshot, issues=(replace(waiting, state="worked here", worker="claude", local=True),)
    )
    assert 'class="flag feedback"' not in client.get("/?fragment=1").text
    worker = replace(snapshot.workers[0], state="limited", merge_conflict=True)
    assert 'class="lane waiting"' in _agent_row((worker,), NOW, {}, {})
    active = _agent_row((replace(worker, state="working", merge_conflict=False),), NOW, {}, {})
    assert 'class="lane waiting"' not in active and 'title="Merge conflict"' not in active


def test_remote_paused_claim_has_gray_box_and_red_merge_chip() -> None:
    claim = ClaimView(
        "o/r",
        1,
        "remote/w",
        False,
        "paused",
        "implement",
        NOW,
        NOW,
        "b",
        NOW,
        merge_conflict=True,
        wait_reason="Rate limit",
    )
    html = _remote_row((claim,), NOW, {}, {})
    assert 'class="lane remote waiting"' in html
    assert "Rate limit" in html and 'title="Merge conflict"' in html


def test_demo_contains_feedback_resource_and_merge_conflict_examples() -> None:
    backend = DemoBackend()
    snapshot = backend.snapshot()
    assert any(i.state == "waiting for your answer" for i in snapshot.issues)
    assert any(w.state == "limited" for w in snapshot.workers)
    assert any(w.merge_conflict for w in snapshot.workers)
    assert any(c.state == "paused" for c in snapshot.claims)
    assert any(w.state == "idle" for w in snapshot.workers)
    assert snapshot.manager_id == MANAGER


@pytest.mark.parametrize("state", ["idle", "paused", "limited"])
def test_waiting_instance_is_gray_while_another_instance_keeps_running(state: WorkerState) -> None:
    active = DemoBackend().snapshot().workers[0]
    waiting = replace(active, name=active.name + ".2", state=state, issue=None, repository=None)
    html = _agent_row((active, waiting), NOW, {}, {})
    assert 'class="lane waiting"' not in html
    assert 'class="run waiting"' in html
    assert 'class="now"' in html
