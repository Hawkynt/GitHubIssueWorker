from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.application.manager import RepositoryWatch
from github_issue_worker.configuration import parse
from github_issue_worker.domain.claim import ClaimState, Holder
from github_issue_worker.domain.work import RunKind
from github_issue_worker.wiring import Runtime
from tests.application.run_fakes import FakeHost, FakeWorkspace
from tests.application.test_manager import ME, POLICY, T0, Fixture, issue, slot
from tests.fakes import InMemoryClaimStore


def held(
    f: Fixture,
    number: int,
    manager: str = "offline",
    state: ClaimState = ClaimState.WORKING,
    age: timedelta = timedelta(),
) -> None:
    f.host.issues[number] = issue(number)
    f.store.force_create(
        POLICY.new_claim(
            number, Holder(manager, f"holder-{number}"), T0 - age, f"issue/{number}"
        ).replace(state=state)
    )


@pytest.mark.parametrize("manager", [ME, "remote", "offline"])
@pytest.mark.parametrize("state", [ClaimState.WORKING, ClaimState.PAUSED])
@pytest.mark.parametrize("count", [1, 2])
def test_full_or_overfull_repository_counts_all_visible_holders_outside_poll_results(
    manager: str, state: ClaimState, count: int
) -> None:
    f = Fixture([slot("free")])
    f.watch.max_active_issues_per_repository = 1
    for number in range(10, 10 + count):
        held(f, number, manager, state)
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.store.current(1) is None
    assert f.runs == []
    assert f.host.worker_label[1] == "worker:queued"


@pytest.mark.parametrize(
    ("state", "age", "blocks"),
    [
        (ClaimState.WORKING, timedelta(days=7), True),
        (ClaimState.PAUSED, timedelta(days=8, microseconds=-1), True),
        (ClaimState.WORKING, timedelta(days=8), False),
        (ClaimState.PAUSED, timedelta(days=9), False),
        (ClaimState.RELEASED, timedelta(), False),
        (ClaimState.DONE, timedelta(), False),
    ],
)
def test_capacity_uses_claim_lifecycle_and_server_time_grace_boundary(
    state: ClaimState, age: timedelta, blocks: bool
) -> None:
    f = Fixture([slot("free")])
    f.watch.max_active_issues_per_repository = 1
    held(f, 10, state=state, age=age)
    f.new_issues(issue(1))
    f.manager.tick()
    assert (f.store.current(1) is None) == blocks


def test_successful_acquisitions_fill_capacity_in_the_same_scheduling_pass() -> None:
    f = Fixture([slot("a", account="a"), slot("b", account="b"), slot("c", account="c")])
    f.watch.max_active_issues_per_repository = 2
    # Leave jobs running so subsequent candidates observe their live claims.
    jobs = []
    f.manager.spawn = jobs.append
    f.new_issues(issue(1), issue(2, minutes=1), issue(3, minutes=2))
    f.manager.tick()
    assert len(jobs) == 2
    assert f.store.current(1) is not None
    assert f.store.current(2) is not None
    assert f.store.current(3) is None


@pytest.mark.parametrize("freed_by", ["released", "done", "deleted", "abandoned"])
def test_capacity_deferred_candidate_retries_without_a_new_issue_event(freed_by: str) -> None:
    f = Fixture([slot("free")])
    f.watch.max_active_issues_per_repository = 1
    held(f, 10)
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.store.current(1) is None
    stored = f.store.read(10)
    assert stored is not None
    if freed_by == "deleted":
        f.store.delete(10, stored.version)
    elif freed_by == "abandoned":
        f.clock.advance(timedelta(days=8))
    else:
        f.store.replace(stored.version, stored.claim.replace(state=ClaimState(freed_by)))
    f.manager.tick()
    assert [run.issue for run in f.runs] == [1]


@pytest.mark.parametrize("state", [ClaimState.WORKING, ClaimState.PAUSED])
def test_owned_claim_continues_or_resumes_when_repository_is_overfull(state: ClaimState) -> None:
    f = Fixture([slot("free")])
    f.watch.max_active_issues_per_repository = 1
    held(f, 10)
    f.store.force_create(
        POLICY.new_claim(1, Holder(ME, "free"), T0, "issue/1").replace(state=state)
    )
    f.new_issues(issue(1))
    f.manager.tick()
    assert len(f.runs) == 1
    assert f.runs[0].kind == RunKind.CONTINUATION


def test_a_full_repository_does_not_block_another_repository() -> None:
    f = Fixture([slot("free")])
    f.watch.max_active_issues_per_repository = 1
    held(f, 10)
    other_store = InMemoryClaimStore()
    other = RepositoryWatch(
        name="o/other",
        host=FakeHost(issues={1: issue(1)}),
        store=other_store,
        coordinator=Coordinator(other_store, f.clock, POLICY),
        max_active_issues_per_repository=1,
    )
    f.manager.repositories.append(other)
    f.new_issues(issue(1))
    f.manager._pending[other.name] = {1}
    f.manager.tick()
    assert f.store.current(1) is None
    assert other_store.current(1) is not None


def test_managers_racing_below_capacity_may_exceed_soft_limit_then_defer() -> None:
    first = Fixture([slot("a")])
    second = Fixture([slot("b")])
    first.watch.max_active_issues_per_repository = 1
    second.watch.max_active_issues_per_repository = 1
    second.store = first.store
    second.watch.store = first.store
    second.watch.coordinator = Coordinator(first.store, second.clock, POLICY)
    second.manager.manager_id = "second"
    first.manager.spawn = lambda job: None
    second.manager.spawn = lambda job: None
    first.new_issues(issue(1))
    second.new_issues(issue(2))
    first.store.interleave_before_write = second.manager.tick
    first.manager.tick()
    assert first.store.current(1) is not None
    assert first.store.current(2) is not None
    # Once the excess is visible, even another idle worker cannot claim.
    first.manager.workers.append(slot("c", account="c"))
    first.new_issues(issue(3))
    first.manager.tick()
    assert first.store.current(3) is None


def test_same_issue_race_still_has_only_one_claim_winner() -> None:
    first = Fixture([slot("a")])
    second = Fixture([slot("b")])
    first.watch.max_active_issues_per_repository = 1
    second.watch.max_active_issues_per_repository = 1
    second.watch.store = first.store
    second.watch.coordinator = Coordinator(first.store, second.clock, POLICY)
    second.manager.manager_id = "second"
    first.manager.spawn = lambda job: None
    second.manager.spawn = lambda job: None
    first.new_issues(issue(1))
    second.new_issues(issue(1))
    first.store.interleave_before_write = second.manager.tick
    first.manager.tick()
    claim = first.store.current(1)
    assert claim is not None and claim.holder.manager == "second"
    assert first.manager.workers[0].current is None


@pytest.mark.parametrize(
    ("default", "override", "expected"),
    [
        (None, None, 4),
        (2, None, 2),
        (2, 1, 1),
    ],
)
@pytest.mark.parametrize("update", ["default", "override", "remove-override"])
def test_effective_repository_capacity_is_wired_initially_and_updated_live(
    tmp_path: Path, default: int | None, override: int | None, expected: int, update: str
) -> None:
    import json

    repository: dict[str, object] = {"repository": "o/r"}
    if override is not None:
        repository["maxActiveIssuesPerRepository"] = override
    body: dict[str, object] = {"repositories": [repository]}
    if default is not None:
        body["defaults"] = {"maxActiveIssuesPerRepository": default}
    config = parse(json.dumps(body))
    f = Fixture([slot("free")])
    runtime = Runtime(
        config=config,
        home=tmp_path,
        api=SimpleNamespace(clock=f.clock),
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id=ME,
        workspace=FakeWorkspace(tmp_path),
    )
    watch = runtime.watch(config.repositories[0])
    assert watch.max_active_issues_per_repository == expected
    f.manager.repositories = [watch]
    if update == "override":
        repository["maxActiveIssuesPerRepository"] = 3
    else:
        body["defaults"] = {"maxActiveIssuesPerRepository": 3}
        if update == "remove-override":
            repository.pop("maxActiveIssuesPerRepository", None)
    live_limit = override if update == "default" and override is not None else 3
    runtime.apply(parse(json.dumps(body)), f.manager)
    assert runtime.watches["o/r"] is watch
    assert watch.max_active_issues_per_repository == live_limit
    # Verify the new value controls scheduling rather than merely being stored.
    watch.store = f.store
    watch.coordinator = f.coordinator
    watch.host = f.host
    f.manager.workers = [slot("free")]
    held(f, 10)
    held(f, 11)
    f.new_issues(issue(1))
    f.manager.tick()
    assert (f.store.current(1) is not None) == (live_limit > 2)
