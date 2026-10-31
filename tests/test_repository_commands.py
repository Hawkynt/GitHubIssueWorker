from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.application.poller import PollState
from github_issue_worker.cli import main
from github_issue_worker.configuration import Configuration, RepositorySettings, save
from github_issue_worker.domain.issue import Actor, Issue
from github_issue_worker.ports.github import GhUnavailable
from tests.fakes import FakeClock, InMemoryClaimStore

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def test_list_reads_status_without_starting_workers_and_honors_state_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    save(Configuration(repositories=[RepositorySettings(repository="o/r")]), config_path)
    state_path = tmp_path / "state" / "state.json"
    state = JsonStateStore(state_path)
    state.set_poll_state(
        "o/r", PollState(watched_since=NOW - timedelta(days=2), last_polled_at=NOW)
    )
    issue = Issue(7, "Current issue", "", "open", Actor("owner"), frozenset(), False, NOW, NOW)
    watch = SimpleNamespace(
        host=SimpleNamespace(open_issues=lambda: [issue]), store=InMemoryClaimStore()
    )
    factory_args = []

    def factory(
        config: Configuration, home: Path, selected_state: Path | None, cache: Path | None
    ) -> SimpleNamespace:
        assert selected_state is not None
        factory_args.append((selected_state, cache))
        return SimpleNamespace(
            state=JsonStateStore(selected_state),
            watch=lambda settings: watch,
            api=SimpleNamespace(clock=FakeClock(NOW)),
        )

    assert (
        main(
            ["--config", str(config_path), "--state-dir", str(state_path.parent), "list"],
            runtime_factory=factory,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "o/r" in out and "2026-10-31 12:00" in out and "#7" in out
    assert "Current issue" in out and "queued=1" in out
    assert factory_args == [(state_path, None)]


def test_list_with_no_repositories_does_not_build_a_runtime(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object) -> None:
        raise AssertionError("an empty list needs no runtime")

    assert main(["--config", str(tmp_path / "absent.json"), "list"], runtime_factory=forbidden) == 0
    assert "no repositories watched" in capsys.readouterr().out


def test_remove_normalizes_the_repository_and_uses_the_acknowledged_control_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import github_issue_worker.adapters.control as control

    path = tmp_path / "config.json"
    save(Configuration(repositories=[RepositorySettings(repository="o/r")]), path)
    calls = []

    def route(directory: Path, config: Path, repository: str, offline: Callable[[], None]) -> None:
        calls.append((directory, config, repository))
        offline()

    def remove(repository: str, config: Path) -> None:
        assert repository == "o/r" and config == path
        save(Configuration(), path)

    monkeypatch.setattr(control, "remove_or_request", route)
    runtime = SimpleNamespace(remove_repository=remove)
    assert (
        main(
            [
                "--config",
                str(path),
                "--state-dir",
                str(tmp_path / "state"),
                "remove",
                "https://github.com/o/r.git",
            ],
            runtime_factory=lambda *args: runtime,
        )
        == 0
    )
    assert calls == [(tmp_path / "state", path, "o/r")]
    assert "no longer watching o/r" in capsys.readouterr().out


def test_remove_live_failure_does_not_fall_back_or_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import github_issue_worker.adapters.control as control

    def refused(*args: object) -> None:
        raise control.ControlError("checkpoint failed; retry")

    def forbidden(*args: object) -> None:
        raise AssertionError("must not build an offline runtime")

    monkeypatch.setattr(control, "remove_or_request", refused)
    assert (
        main(
            ["--config", str(tmp_path / "config.json"), "remove", "o/r"], runtime_factory=forbidden
        )
        == 2
    )
    output = capsys.readouterr()
    assert "checkpoint failed" in output.err and "no longer watching" not in output.out


@pytest.mark.parametrize("failure", [OSError("offline"), GhUnavailable("offline")])
def test_failed_live_removal_reload_is_retried_without_forgetting_the_change(
    tmp_path: Path, failure: Exception
) -> None:
    from github_issue_worker.cli import _mtime, _reload_if_changed

    path = tmp_path / "config.json"
    save(Configuration(), path)
    calls = []

    def apply(config: Configuration, manager: object) -> None:
        calls.append(config)
        if len(calls) == 1:
            raise failure

    runtime = SimpleNamespace(apply=apply)
    assert _reload_if_changed(runtime, SimpleNamespace(), path, 0.0) == 0.0
    assert _reload_if_changed(runtime, SimpleNamespace(), path, 0.0) == _mtime(path)
    assert len(calls) == 2
