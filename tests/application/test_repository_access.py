from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from github_issue_worker.application.manager import RepositoryWatch
from github_issue_worker.application.poller import PollState
from github_issue_worker.domain.claim import Holder
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentRun, RunReport
from github_issue_worker.ports.claims import StoredClaim
from github_issue_worker.ports.repository import RepositoryInfo
from tests.application.test_manager import ME, T0, Fixture, Recorded, issue, slot


def access(f: Fixture, allowed: bool = True) -> Mock:
    probe = Mock(return_value=RepositoryInfo("main", allowed, ("squash",)))
    f.host.info = probe  # type: ignore[method-assign]
    return probe


def test_given_saved_state_when_starting_then_check_before_labels_adoption_and_polling() -> None:
    f = Fixture([slot("w")])
    f.manager.poll_states[f.watch.name] = PollState(watched_since=T0)
    calls: list[str] = []
    probe = access(f)
    probe.side_effect = lambda: calls.append("access") or RepositoryInfo("main", True, ("squash",))
    f.host.ensure_worker_labels = lambda: calls.append("labels")  # type: ignore[method-assign]
    original = f.store.issues
    f.store.issues = lambda: calls.append("adopt") or original()  # type: ignore[method-assign]
    f.manager.poll = lambda *args: calls.append("poll") or f.poll(*args)
    f.manager.start()
    assert calls == ["access", "labels"]
    f.manager.tick()
    assert calls == ["access", "labels", "adopt", "poll"]
    assert probe.call_count == 1


def test_given_a_restart_when_starting_then_access_is_checked_again() -> None:
    f = Fixture([])
    probe = access(f)
    f.manager.start()
    restarted = replace(f.manager)
    restarted.start()
    assert probe.call_count == 2


def test_given_push_access_when_starting_then_normal_work_is_enabled() -> None:
    f = Fixture([slot("w")])
    probe = access(f)
    f.new_issues(issue(1))
    f.manager.tick()
    assert probe.call_count == 1
    assert f.runs == [Recorded("w", 1, RunKind.FIRST)]


@pytest.mark.parametrize("housekeeping", [False, True])
def test_given_an_idle_repository_when_an_hour_elapses_then_recheck_before_polling(
    housekeeping: bool,
) -> None:
    f = Fixture([])
    probe = access(f)
    if housekeeping:
        f.manager.sweep = Mock()
        f.manager.sweep_every = timedelta(days=1)
    f.manager.tick()
    assert probe.call_count == 1
    f.clock.advance(timedelta(hours=1) - timedelta(microseconds=1))
    f.manager.tick()
    assert probe.call_count == 1
    f.clock.advance(timedelta(microseconds=1))
    probe.return_value = RepositoryInfo("main", False, ("squash",))
    f.manager.poll = Mock(side_effect=AssertionError("must suspend before polling"))
    f.manager.tick()
    assert probe.call_count == 2
    f.manager.poll.assert_not_called()


@pytest.mark.parametrize("startup", [True, False])
def test_given_lost_push_access_when_checked_then_suspend_log_and_keep_configuration(
    startup: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    f = Fixture([])
    probe = access(f)
    if not startup:
        f.manager.tick()
        f.clock.advance(timedelta(hours=1))
    probe.return_value = RepositoryInfo("main", False, ("squash",))
    labels = Mock()
    f.host.ensure_worker_labels = labels  # type: ignore[method-assign]
    f.manager.tick()
    assert f.manager.repositories == [f.watch]
    assert f.watch.suspension_reason
    assert f.watch.name in caplog.text and "push access" in caplog.text
    assert "suspend" in caplog.text
    labels.assert_not_called()


def test_given_suspended_work_when_ticking_then_no_processing_or_cleanup() -> None:
    f = Fixture([slot("w")])
    probe = access(f, False)
    handle = f.coordinator.claim(1, Holder(ME, "w"), "issue/1")
    assert handle
    f.new_issues(issue(1), issue(2))
    before = f.store.read(1)
    f.manager.sweep = Mock()
    f.manager.poll = Mock(side_effect=AssertionError("no normal polling"))
    f.store.issues = Mock(side_effect=AssertionError("no claim adoption"))  # type: ignore[method-assign]
    f.manager.tick()
    f.manager.tick()
    assert f.store.read(1) == before
    assert f.store.read(2) is None
    assert not f.runs
    assert probe.call_count == 1
    assert f.watch.suspension_reason
    f.manager.poll.assert_not_called()
    f.manager.sweep.assert_not_called()


@pytest.mark.parametrize("broken", [False, True])
def test_given_one_inaccessible_repository_when_ticking_then_other_repositories_continue(
    broken: bool,
) -> None:
    first, second = Fixture([]), Fixture([slot("w")])
    probe = access(first, False)
    if broken:
        probe.side_effect = RuntimeError("connection unavailable")
    second.new_issues(issue(1))
    second.manager.repositories.insert(0, first.watch)
    second.manager.tick()
    assert second.runs == [Recorded("w", 1, RunKind.FIRST)]
    assert first.watch.suspension_reason


@pytest.mark.parametrize("restart", [False, True])
def test_given_suspension_when_access_returns_then_resume_without_readding(restart: bool) -> None:
    f = Fixture([slot("w")])
    probe = access(f, False)
    f.new_issues(issue(1))
    f.manager.tick()
    assert not f.runs
    probe.return_value = RepositoryInfo("main", True, ("squash",))
    if restart:
        f.manager = replace(f.manager)
    else:
        f.clock.advance(timedelta(hours=1))
    f.manager.tick()
    assert not f.watch.suspension_reason
    assert f.runs == [Recorded("w", 1, RunKind.FIRST)]


def test_given_suspension_when_access_stays_absent_then_hourly_checks_continue_without_log_flood(
    caplog: pytest.LogCaptureFixture,
) -> None:
    f = Fixture([])
    probe = access(f, False)
    f.manager.tick()
    for _ in range(2):
        f.clock.advance(timedelta(minutes=30))
        f.manager.tick()
    assert probe.call_count == 2
    assert len([r for r in caplog.records if "suspended" in r.message]) == 1


@pytest.mark.parametrize("previously_allowed", [False, True])
def test_given_a_failed_access_query_when_ticking_then_log_fail_closed_and_retry_next_cycle(
    previously_allowed: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    f = Fixture([slot("w")])
    probe = access(f)
    if previously_allowed:
        f.manager.tick()
        f.clock.advance(timedelta(hours=1))
    caplog.clear()
    probe.side_effect = RuntimeError("connection unavailable")
    f.new_issues(issue(1))
    f.manager.tick()
    assert not f.runs
    assert f.watch.suspension_reason
    assert f.watch.name in caplog.text and "push-access check failed" in caplog.text
    assert "lost push access" not in caplog.text
    probe.side_effect = None
    f.manager.tick()
    assert f.runs == [Recorded("w", 1, RunKind.FIRST)]


def test_given_restored_access_when_initializing_labels_fails_then_retry_without_rechecking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    f = Fixture([])
    probe = access(f)
    labels = Mock(side_effect=[RuntimeError("labels unavailable"), None])
    f.host.ensure_worker_labels = labels  # type: ignore[method-assign]
    f.manager.start()
    f.manager.tick()
    assert labels.call_count == 2
    assert probe.call_count == 1
    assert "labels unavailable" in caplog.text


def test_given_a_running_job_when_suspended_then_keep_its_claim_and_local_history() -> None:
    f = Fixture([slot("w")])
    access(f)
    f.new_issues(issue(1))
    jobs: list[Callable[[], None]] = []
    f.manager.spawn = jobs.append
    f.manager.on_finished = Mock()
    f.manager.tick()
    before = f.store.read(1)
    assert before
    f.watch.suspension_reason = "lost push access"
    f.manager.run_issue = lambda *args: f.watch.require_access()  # type: ignore[assignment,return-value]
    jobs[0]()
    assert f.store.read(1) == before
    assert not f.manager.workers[0].busy
    assert f.manager._own[(f.watch.name, 1)] == "w"
    f.manager.on_finished.assert_called_once()
    assert f.manager.on_finished.call_args.args[0].verdict == "suspended"


def test_given_suspended_running_work_when_agent_returns_then_no_renewal_or_shipping(
    tmp_path: Path,
) -> None:
    from github_issue_worker.application.issue_run import IssueRun, RunSettings
    from tests.application.test_issue_run import Harness, ok, step

    h = Harness(tmp_path, [])
    watch = RepositoryWatch(h.host.repository, h.host, h.coordinator, h.store)
    coordinator = replace(h.coordinator, before_write=watch.require_access)
    before = h.store.read(42)

    def finish(run: AgentRun) -> RunReport:
        nonlocal before
        before = h.store.read(42)
        watch.suspension_reason = "lost push access"
        return step(h.workspace, ok())(run)

    verdict = IssueRun(
        host=h.host,
        workspace=h.workspace,
        coordinator=coordinator,
        agent=finish,
        verify=h.verify,
        sessions=h.sessions,
        clock=h.clock,
        sleep=lambda seconds: None,
        settings=RunSettings(understand_first=False),
    ).run(h.handle, h.issue, (), RunKind.FIRST)
    assert verdict.kind == "suspended"
    assert before is not None and before.claim.step == "implement"
    assert h.store.read(42) == before
    assert h.workspace.trees[42]  # local work survives
    assert not h.workspace.pushes and not h.host.pulls and not h.host.merged
    assert not h.host.posted


@pytest.mark.parametrize("operation", ["claim", "renew", "fence", "record_run", "complete"])
def test_given_suspension_when_a_claim_write_is_attempted_then_no_remote_change(
    operation: str,
) -> None:
    from github_issue_worker.domain.claim import RunEntry
    from github_issue_worker.ports.repository import RepositorySuspended

    f = Fixture([])
    handle = f.coordinator.claim(1, Holder(ME, "w"), "issue/1")
    assert handle
    coordinator = replace(f.coordinator, before_write=f.watch.require_access)
    before = f.store.read(1)
    f.watch.suspension_reason = "lost push access"
    with pytest.raises(RepositorySuspended):
        if operation == "claim":
            coordinator.claim(2, Holder(ME, "w"), "issue/2")
        elif operation == "record_run":
            coordinator.record_run(1, RunEntry(ME, "w", "codex", "m", "first", "suspended", T0, T0))
        else:
            getattr(coordinator, operation)(handle)
    assert f.store.read(1) == before
    assert f.store.read(2) is None


def test_given_suspension_during_a_fence_read_when_it_returns_then_block_shipping() -> None:
    from github_issue_worker.ports.repository import RepositorySuspended

    f = Fixture([])
    handle = f.coordinator.claim(1, Holder(ME, "w"), "issue/1")
    assert handle
    coordinator = replace(f.coordinator, before_write=f.watch.require_access)
    original = f.store.read

    def read(number: int) -> StoredClaim | None:
        current = original(number)
        f.watch.suspension_reason = "lost push access"
        return current

    f.store.read = read  # type: ignore[method-assign]
    with pytest.raises(RepositorySuspended):
        coordinator.fence(handle)


def test_given_a_suspended_running_job_when_access_returns_then_resume_its_existing_claim() -> None:
    f = Fixture([slot("w")])
    probe = access(f)
    f.new_issues(issue(1))
    jobs: list[Callable[[], None]] = []
    f.manager.spawn = jobs.append
    f.manager.tick()
    before = f.store.read(1)
    assert before
    f.clock.advance(timedelta(hours=1))
    probe.return_value = RepositoryInfo("main", False, ("squash",))
    f.manager.tick()
    f.manager.run_issue = lambda *args: f.watch.require_access()  # type: ignore[assignment,return-value]
    jobs.pop(0)()
    f.clock.advance(timedelta(hours=1))
    probe.return_value = RepositoryInfo("main", True, ("squash",))
    f.manager.run_issue = f.run_issue
    f.manager.tick()
    assert len(jobs) == 1
    assert f.store.read(1) == before
    jobs[0]()
    assert f.runs == [Recorded("w", 1, RunKind.CONTINUATION)]


def test_given_a_readded_watch_when_processing_then_check_again_before_any_writes() -> None:
    f = Fixture([])
    probe = access(f)
    f.manager.start()
    probe.return_value = RepositoryInfo("main", False, ("squash",))
    readded = replace(f.watch)
    f.manager.repositories = [readded]
    f.manager.tick()
    assert probe.call_count == 2
    assert readded.suspension_reason == "lost push access"
