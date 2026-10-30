from __future__ import annotations

from datetime import UTC, datetime, timedelta

from github_issue_worker.adapters.server_clock import ServerClock
from tests.fakes import FakeClock

LOCAL = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def test_without_an_observation_it_falls_back_to_the_local_clock() -> None:
    assert ServerClock(local=FakeClock(LOCAL)).now() == LOCAL


def test_it_follows_the_server_even_when_the_local_clock_is_a_day_off() -> None:
    local = FakeClock(LOCAL + timedelta(days=1))  # this machine's clock runs a day fast
    clock = ServerClock(local=local)
    clock.observe("Fri, 30 Oct 2026 12:00:00 GMT")
    assert clock.now() == LOCAL

    local.advance(timedelta(hours=3))
    assert clock.now() == LOCAL + timedelta(hours=3)


def test_a_newer_observation_replaces_the_offset() -> None:
    local = FakeClock(LOCAL)
    clock = ServerClock(local=local)
    clock.observe("Fri, 30 Oct 2026 12:00:10 GMT")
    assert clock.now() == LOCAL + timedelta(seconds=10)
    clock.observe("Fri, 30 Oct 2026 11:59:55 GMT")
    assert clock.now() == LOCAL - timedelta(seconds=5)


def test_an_unparsable_header_is_ignored() -> None:
    clock = ServerClock(local=FakeClock(LOCAL))
    clock.observe("yesterday-ish")
    assert clock.now() == LOCAL
