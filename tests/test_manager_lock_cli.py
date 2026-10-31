"""Configuration ownership is enforced before the manager's first side effect (F3.6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from github_issue_worker.adapters.manager_lock import AlreadyRunning, manager_lock
from github_issue_worker.cli import main
from github_issue_worker.configuration import Configuration, default_path, save
from tests.test_cli import Api, Manager, Runtime


@pytest.mark.parametrize("command", [["watch", "--no-ui"], ["watch", "--once"], ["once"]])
def test_given_an_owner_when_any_watch_form_starts_then_it_reports_pid_without_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    path = tmp_path / "missing.json"

    def forbidden(*args: object) -> Runtime:
        pytest.fail("duplicate initialized the runtime")

    with manager_lock(path):
        assert main(["--config", str(path), *command], runtime_factory=forbidden) != 0
        assert str(os.getpid()) in capsys.readouterr().err
        assert not path.exists()
        with pytest.raises(AlreadyRunning), manager_lock(path):
            pytest.fail("duplicate released the owner lock")


@pytest.mark.parametrize("alias", ["relative", "symlink", "default"])
def test_given_path_aliases_when_watch_starts_then_it_identifies_the_same_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    path = default_path(home=tmp_path)
    save(Configuration(), path)
    monkeypatch.chdir(tmp_path)
    if alias == "relative":
        argv = ["--config", str(path.relative_to(tmp_path))]
    elif alias == "symlink":
        link = tmp_path / "alias.json"
        try:
            link.symlink_to(path)
        except OSError:
            pytest.skip("symlinks require permission on this Windows installation")
        argv = ["--config", str(link)]
    else:
        argv = []
    with manager_lock(path):
        assert (
            main(
                [
                    *argv,
                    "--state-dir",
                    str(tmp_path / "other"),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                    "watch",
                    "--once",
                    "--no-ui",
                ],
                home=tmp_path,
                runtime_factory=lambda *args: pytest.fail("alias bypassed lock"),
            )
            != 0
        )


def test_given_an_atomic_configuration_save_when_watch_restarts_then_ownership_is_preserved(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    save(Configuration(), path)
    with manager_lock(path):
        save(Configuration(), path)
        assert (
            main(
                ["--config", str(path), "once"],
                runtime_factory=lambda *args: pytest.fail("save bypassed lock"),
            )
            != 0
        )


@pytest.mark.parametrize("failure", ["none", "runtime", "auth", "start", "tick"])
def test_given_startup_or_execution_finishes_when_restarting_then_ownership_is_released(
    tmp_path: Path,
    failure: str,
) -> None:
    path = tmp_path / "config.json"
    save(Configuration(), path)

    class FailingManager(Manager):
        def start(self) -> None:
            if failure == "start":
                raise RuntimeError("start failed")
            super().start()

        def tick(self) -> None:
            if failure == "tick":
                raise RuntimeError("tick failed")
            super().tick()

    def factory(*args: object) -> Runtime:
        # Ownership precedes runtime construction, not merely the polling loop.
        with pytest.raises(AlreadyRunning), manager_lock(path):
            pytest.fail("runtime started without ownership")
        if failure == "runtime":
            raise RuntimeError("runtime failed")
        return Runtime(Api(logged_in=failure != "auth"), FailingManager())

    if failure in {"runtime", "start", "tick"}:
        with pytest.raises(RuntimeError, match="failed"):
            main(["--config", str(path), "once"], runtime_factory=factory)
    else:
        assert main(["--config", str(path), "once"], runtime_factory=factory) == (
            2 if failure == "auth" else 0
        )
    for command in (["once"], ["watch", "--once"]):
        assert (
            main(
                ["--config", str(path), *command],
                runtime_factory=lambda *args: Runtime(Api(), Manager()),
            )
            == 0
        )


def test_given_a_lock_io_error_when_watch_starts_then_it_fails_closed_with_a_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from github_issue_worker.adapters import manager_lock as adapter

    def denied() -> str:
        raise PermissionError("lock directory denied")

    monkeypatch.setattr(adapter.tempfile, "gettempdir", denied)
    assert (
        main(
            ["--config", str(tmp_path / "config.json"), "once"],
            runtime_factory=lambda *args: pytest.fail("unprotected startup"),
        )
        != 0
    )
    assert "lock" in capsys.readouterr().err.lower()


def test_given_running_work_when_watch_shuts_down_then_lock_covers_drain_and_ui_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from github_issue_worker import cli

    path = tmp_path / "config.json"
    save(Configuration(), path)
    observed: list[str] = []

    def owned(phase: str) -> None:
        with pytest.raises(AlreadyRunning), manager_lock(path):
            pytest.fail(f"ownership lost during {phase}")
        observed.append(phase)

    class Drain:
        @property
        def busy(self) -> bool:
            owned("drain")
            return False

    class StoppingManager(Manager):
        def start(self) -> None:
            owned("start")
            self.workers = [Drain()]

        def tick(self) -> None:
            owned("tick")
            raise KeyboardInterrupt

    def start_ui(*args: object, **kwargs: object) -> SimpleNamespace:
        owned("ui")
        return SimpleNamespace(stop=lambda: owned("ui-stop"))

    monkeypatch.setattr(cli, "_start_ui", start_ui)
    assert (
        main(
            ["--config", str(path), "watch"],
            runtime_factory=lambda *args: Runtime(Api(), StoppingManager()),
        )
        == 0
    )
    assert observed == ["start", "ui", "tick", "drain", "ui-stop"]
    with manager_lock(path):
        pass


def test_given_a_symlink_retargeted_during_startup_when_watch_runs_then_it_uses_locked_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from github_issue_worker.adapters import manager_lock as adapter

    first, second, link = (tmp_path / name for name in ("first.json", "second.json", "alias.json"))
    first.write_text('{"manager": {"name": "first"}}')
    second.write_text('{"manager": {"name": "second"}}')
    try:
        link.symlink_to(first)
    except OSError:
        pytest.skip("symlinks require permission on this Windows installation")
    original = adapter._try_lock
    retargeted = False

    def retarget(stream: object) -> bool:
        nonlocal retargeted
        result = original(stream)
        if not retargeted:
            link.unlink()
            link.symlink_to(second)
            retargeted = True
        return result

    monkeypatch.setattr(adapter, "_try_lock", retarget)

    def factory(config: Configuration, *args: object) -> Runtime:
        assert config.manager.name == "first"
        return Runtime(Api(), Manager())

    assert main(["--config", str(link), "once"], runtime_factory=factory) == 0


def test_given_invalid_configuration_when_an_owner_exists_then_duplicate_reports_owner_first(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.json"
    path.write_text("invalid json")
    with manager_lock(path):
        assert main(["--config", str(path), "once"]) != 0
        assert f"PID {os.getpid()}" in capsys.readouterr().err


def test_given_pid_publication_fails_when_restarting_then_partial_ownership_is_released(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from github_issue_worker.adapters import manager_lock as adapter
    from github_issue_worker.adapters.manager_lock import ManagerLockError

    path = tmp_path / "config.json"

    def fail() -> int:
        raise OSError("PID publication failed")

    with monkeypatch.context() as patch:
        patch.setattr(adapter.os, "getpid", fail)
        with pytest.raises(ManagerLockError, match="PID publication failed"), manager_lock(path):
            pytest.fail("entered without publishing owner")
    with manager_lock(path):
        pass


@pytest.mark.parametrize("command", ["watch", "once"])
def test_given_manager_command_help_when_requested_then_it_explains_duplicate_pid(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as result:
        main([command, "--help"])
    assert result.value.code == 0
    help_text = capsys.readouterr().out
    assert "configuration" in help_text
    assert "PID" in help_text
