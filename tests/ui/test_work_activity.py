from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from github_issue_worker.application.history import RunRecord
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
