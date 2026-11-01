from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from fastapi.testclient import TestClient

from github_issue_worker.application.history import RunRecord
from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.pages import cockpit_fragment


def test_quota_only_history_is_absent_from_every_cockpit_chart() -> None:
    snapshot = DemoBackend().snapshot()
    ping = RunRecord(
        "o/r",
        1,
        "quota-worker",
        "antigravity",
        "Quota-only model",
        "continuation",
        "paused",
        snapshot.now - timedelta(minutes=1),
        snapshot.now,
        snapshot.now,
        active=False,
    )
    html = cockpit_fragment(snapshot, [ping])
    assert "Quota-only model" not in html
    html = cockpit_fragment(snapshot, [replace(ping, active=True)])
    assert "Quota-only model" in html


def test_a_live_quota_probe_does_not_draw_an_active_flight_log_span() -> None:
    snapshot = DemoBackend().snapshot()
    worker = replace(
        snapshot.workers[0],
        state="working",
        model="Quota-only model",
        since=snapshot.now - timedelta(minutes=1),
        active=False,
    )
    snapshot = replace(snapshot, workers=(worker,))
    flight = cockpit_fragment(snapshot, []).partition('id="flight"')[2].partition("</section>")[0]
    assert "Quota-only model" not in flight
    snapshot = replace(snapshot, workers=(replace(worker, active=True),))
    flight = cockpit_fragment(snapshot, []).partition('id="flight"')[2].partition("</section>")[0]
    assert "Quota-only model" in flight


def test_older_real_work_is_accessible_without_erasing_a_workers_lane() -> None:
    backend = DemoBackend()
    snap = backend.snapshot()
    older = RunRecord(
        "o/r",
        4242,
        snap.workers[0].name,
        "codex",
        "Historical Model",
        "first",
        "merged",
        snap.now - timedelta(days=2, minutes=10),
        snap.now - timedelta(days=2),
        snap.now - timedelta(days=2),
    )
    backend._history = [older]
    client = TestClient(create_app(backend))
    day = (
        client.get("/workers?fragment=1")
        .text.partition('id="flight"')[2]
        .partition("</section>")[0]
    )
    assert 'class="row-name"' in day and snap.workers[0].name in day
    assert "o/r#4242" not in day
    for window in ("week", "month", "all"):
        html = client.get(f"/workers?flight={window}&fragment=1").text
        flight = html.partition('id="flight"')[2].partition("</section>")[0]
        assert "o/r#4242" in flight
        assert f"flight={window}" in html  # other chart switches preserve the selected window
    assert client.get("/workers?flight=invalid").status_code == 422
