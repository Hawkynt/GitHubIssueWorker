from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from github_issue_worker.adapters.control import (
    ControlError,
    ControlLease,
    ControlServer,
    remove_or_request,
)

pytestmark = pytest.mark.os_integration


def test_offline_removal_runs_directly_and_a_stale_descriptor_is_harmless(tmp_path: Path) -> None:
    (tmp_path / "control.json").write_text("stale", encoding="utf-8")
    calls = []
    remove_or_request(tmp_path, tmp_path / "config.json", "o/r", lambda: calls.append("offline"))
    assert calls == ["offline"]
    with ControlLease(tmp_path):
        pass


def test_live_removal_is_authenticated_and_acknowledged_before_returning(tmp_path: Path) -> None:
    calls = []
    config = tmp_path / "config.json"
    with ControlLease(tmp_path), ControlServer(tmp_path, config, lambda repo: calls.append(repo)):
        remove_or_request(
            tmp_path, config, "o/r", lambda: pytest.fail("live manager must own removal")
        )
        assert calls == ["o/r"]
        descriptor = json.loads((tmp_path / "control.json").read_text())
        descriptor["token"] = "incorrect"
        (tmp_path / "control.json").write_text(json.dumps(descriptor), encoding="utf-8")
        with pytest.raises(ControlError, match="403"):
            remove_or_request(tmp_path, config, "o/r", lambda: pytest.fail("must not fall back"))
        assert calls == ["o/r"]
    assert not (tmp_path / "control.json").exists()


def test_live_failure_and_configuration_mismatch_never_fall_back_offline(tmp_path: Path) -> None:
    def fail(repo: str) -> None:
        raise ValueError("checkpoint unavailable")

    config = tmp_path / "config.json"
    with ControlLease(tmp_path), ControlServer(tmp_path, config, fail):
        with pytest.raises(ControlError, match="checkpoint unavailable"):
            remove_or_request(tmp_path, config, "o/r", lambda: pytest.fail("unsafe fallback"))
        with pytest.raises(ControlError, match="configuration"):
            remove_or_request(
                tmp_path, tmp_path / "different.json", "o/r", lambda: pytest.fail("wrong manager")
            )


def test_control_lease_excludes_another_process_and_is_released_after_exit(tmp_path: Path) -> None:
    script = (
        "from pathlib import Path; "
        "from github_issue_worker.adapters.control import ControlLease; "
        "import sys; lease=ControlLease(Path(sys.argv[1])); "
        "sys.exit(0 if lease.acquire() else 3)"
    )
    with ControlLease(tmp_path):
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
        assert result.returncode == 3
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert result.returncode == 0


def test_missing_live_descriptor_reports_retry_instead_of_unsafe_direct_removal(
    tmp_path: Path,
) -> None:
    with ControlLease(tmp_path), pytest.raises(ControlError, match="retry"):
        remove_or_request(
            tmp_path, tmp_path / "config.json", "o/r", lambda: pytest.fail("manager is starting")
        )


def test_watch_without_ui_acknowledges_removal_from_a_separate_cli_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from github_issue_worker.adapters.state_store import JsonStateStore
    from github_issue_worker.application.coordinator import Coordinator
    from github_issue_worker.application.issue_run import Verdict
    from github_issue_worker.application.manager import Manager, RepositoryWatch, WorkerSlot
    from github_issue_worker.application.poller import PollResult, PollState
    from github_issue_worker.cli import main
    from github_issue_worker.configuration import Configuration, RepositorySettings, load, save
    from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
    from github_issue_worker.domain.issue import Actor, Issue
    from github_issue_worker.wiring import Runtime
    from tests.application.run_fakes import FakeHost, FakeWorkspace
    from tests.fakes import FakeClock, InMemoryClaimStore

    config = Configuration(repositories=[RepositorySettings(repository="o/r")])
    path = tmp_path / "config.json"
    save(config, path)
    now = datetime(2026, 10, 31, tzinfo=UTC)
    clock = FakeClock(now)
    store = InMemoryClaimStore()
    coordinator = Coordinator(store, clock, ClaimPolicy(timedelta(days=7), timedelta(days=1)))
    coordinator.claim(1, Holder("here", "w"), "issue/1")
    item = Issue(1, "Current", "", "open", Actor("owner"), frozenset(), False, now, now)
    watch = RepositoryWatch("o/r", FakeHost(repository="o/r", issues={1: item}), coordinator, store)
    worker = WorkerSlot("w", "local", "a")
    state = JsonStateStore(tmp_path / "state.json")
    api = SimpleNamespace(clock=clock, request=lambda *args: None)
    runtime = Runtime(
        config,
        tmp_path,
        api,
        state,
        "here",
        FakeWorkspace(tmp_path),
        watches={"o/r": watch},
        slots=[worker],
    )
    started = threading.Event()

    def run(*args: object) -> Verdict:
        started.set()
        assert watch.stopping.wait(10)
        return Verdict("stopped")

    manager = Manager(
        "here",
        frozenset(),
        [watch],
        [worker],
        lambda *args: (PollResult(), PollState(watched_since=now)),
        {},
        state,
        clock,
        (),
        run,
        lambda job: threading.Thread(target=job).start(),
    )
    monkeypatch.setattr(runtime, "manager", lambda: manager)
    result = []

    def remove() -> None:
        assert started.wait(10)
        result.append(
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "github_issue_worker",
                    "--config",
                    str(path),
                    "--state-dir",
                    str(tmp_path),
                    "remove",
                    "o/r",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        )

    remover = threading.Thread(target=remove)
    remover.start()
    try:
        assert (
            main(
                ["--config", str(path), "--state-dir", str(tmp_path), "watch", "--once", "--no-ui"],
                home=tmp_path,
                runtime_factory=lambda *args: runtime,
            )
            == 0
        )
        remover.join(15)
        assert result and result[0].returncode == 0, (
            result[0].stderr if result else "no acknowledgement"
        )
        assert "no longer watching o/r" in result[0].stdout
        assert store.current(1).state is ClaimState.RELEASED
        assert not load(path).repositories and not manager.repositories
    finally:
        watch.stopping.set()
        remover.join(15)


def test_malformed_live_descriptor_reports_retry_without_direct_removal(tmp_path: Path) -> None:
    with ControlLease(tmp_path):
        (tmp_path / "control.json").write_text("[]", encoding="utf-8")
        with pytest.raises(ControlError, match="retry"):
            remove_or_request(
                tmp_path,
                tmp_path / "config.json",
                "o/r",
                lambda: pytest.fail("unsafe direct removal"),
            )
