"""Real process contention and process death, portable to both CI operating systems (F3.6)."""

from __future__ import annotations

import io
import multiprocessing
import os
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from github_issue_worker.adapters.manager_lock import AlreadyRunning, manager_lock
from github_issue_worker.cli import main
from github_issue_worker.configuration import Configuration, save
from tests.test_cli import Api, Manager, Runtime

pytestmark = pytest.mark.os_integration


def _watch_process(connection: Connection, path: Path) -> None:
    """Wait on a start barrier; keep the winner inside runtime construction until released."""
    connection.send(("ready", os.getpid()))
    connection.recv()

    def factory(*args: object) -> Runtime:
        connection.send(("owner", os.getpid()))
        connection.recv()
        return Runtime(Api(), Manager())

    try:
        diagnostic = io.StringIO()
        with redirect_stderr(diagnostic):
            result = main(
                ["--config", str(path), "watch", "--once", "--no-ui"], runtime_factory=factory
            )
        connection.send(("exit", result))
        connection.send(("diagnostic", diagnostic.getvalue()))
    finally:
        connection.close()


@contextmanager
def child(path: Path) -> Iterator[tuple[multiprocessing.process.BaseProcess, Connection]]:
    context = multiprocessing.get_context("spawn")
    parent, peer = context.Pipe()
    process = context.Process(target=_watch_process, args=(peer, path))
    process.start()
    peer.close()
    try:
        assert parent.poll(10), "child failed to reach start barrier"
        assert parent.recv()[0] == "ready"
        yield process, parent
    finally:
        if process.is_alive():
            process.kill()
        process.join(10)
        parent.close()
        assert not process.is_alive()


def message(connection: Connection) -> tuple[str, int]:
    assert connection.poll(10), "child did not respond"
    result: tuple[str, int] = connection.recv()
    return result


@pytest.mark.parametrize("existing", [False, True])
def test_given_simultaneous_watch_starts_when_claiming_configuration_then_one_wins_with_pid(
    tmp_path: Path, existing: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.json"
    if existing:
        save(Configuration(), path)
    with child(path) as (first, a), child(path) as (second, b):
        a.send("start")
        b.send("start")
        outcomes = [message(a), message(b)]
        assert sorted(outcome[0] for outcome in outcomes) == ["exit", "owner"]
        index = next(i for i, outcome in enumerate(outcomes) if outcome[0] == "owner")
        owner, winner = [(first, a), (second, b)][index]
        loser, loser_connection = [(first, a), (second, b)][1 - index]
        assert loser_connection.poll(10)
        assert str(owner.pid) in loser_connection.recv()[1]
        assert outcomes[1 - index][1] != 0
        loser.join(10)
        assert loser.exitcode == 0
        assert owner.is_alive()
        # A fresh CLI contender observes the actual child PID, not a stale or partial PID.
        assert (
            main(
                ["--config", str(path), "once"],
                runtime_factory=lambda *args: pytest.fail("duplicate started"),
            )
            != 0
        )
        assert str(owner.pid) in capsys.readouterr().err
        winner.send("stop")
        assert message(winner) == ("exit", 0)
        owner.join(10)
        assert owner.exitcode == 0


def test_given_a_forcibly_killed_owner_when_watch_restarts_then_no_stale_cleanup_is_needed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    with child(path) as (process, connection):
        connection.send("start")
        assert message(connection) == ("owner", process.pid)
        process.kill()
        process.join(10)
        assert (
            main(
                ["--config", str(path), "once"],
                runtime_factory=lambda *args: Runtime(Api(), Manager()),
            )
            == 0
        )


def test_given_distinct_configurations_when_both_watch_then_each_owns_only_its_configuration(
    tmp_path: Path,
) -> None:
    paths = [tmp_path / "a.json", tmp_path / "b.json"]
    for path in paths:
        save(Configuration(), path)
    with child(paths[0]) as (first, a), child(paths[1]) as (second, b):
        a.send("start")
        b.send("start")
        assert message(a) == ("owner", first.pid)
        assert message(b) == ("owner", second.pid)
        a.send("stop")
        assert message(a) == ("exit", 0)
        first.join(10)
        with manager_lock(paths[0]), pytest.raises(AlreadyRunning), manager_lock(paths[1]):
            pytest.fail("stopping one owner released another")
        b.send("stop")
        assert message(b) == ("exit", 0)


def test_given_local_configuration_owners_when_racing_for_an_issue_then_claims_still_fence(
    tmp_path: Path,
) -> None:
    from github_issue_worker.application.coordinator import Coordinator
    from github_issue_worker.domain.claim import Holder
    from tests.application.test_coordinator import POLICY, T0
    from tests.fakes import FakeClock, InMemoryClaimStore

    store = InMemoryClaimStore()
    clock = FakeClock(T0)
    first = Coordinator(store=store, clock=clock, policy=POLICY)
    second = Coordinator(store=store, clock=clock, policy=POLICY)
    with manager_lock(tmp_path / "a.json"), manager_lock(tmp_path / "b.json"):
        holder = Holder(manager="first", worker="worker")
        other = Holder(manager="second", worker="worker")
        store.interleave_before_write = lambda: store.force_create(
            POLICY.new_claim(issue=68, holder=other, now=T0, branch="issue/68")
        )
        assert first.claim(68, holder, branch="issue/68") is None
        stored = store.current(68)
        assert stored is not None and stored.holder == other
        assert second.claim(68, other, branch="issue/68") is None
