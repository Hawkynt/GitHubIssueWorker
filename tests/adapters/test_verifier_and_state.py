from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.adapters.verifier import Verifier, detect_commands
from github_issue_worker.application.poller import PollState

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("pyproject.toml", ["python -m pytest -q"]),
        ("package.json", ["npm test"]),
        ("Cargo.toml", ["cargo test"]),
        ("App.slnx", ["dotnet test"]),
        ("App.sln", ["dotnet test"]),
    ],
)
def test_commands_are_detected_from_the_stack(
    tmp_path: Path, marker: str, expected: list[str]
) -> None:
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert detect_commands(tmp_path) == expected


def test_configured_commands_win_over_detection(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("", encoding="utf-8")
    assert Verifier(build="make", test="make check").commands(tmp_path) == ["make", "make check"]


def test_no_stack_means_nothing_to_run(tmp_path: Path) -> None:
    assert detect_commands(tmp_path) == []
    ok, output = Verifier().verify(tmp_path)
    assert ok and "no build or test command" in output


def test_verification_runs_in_the_worktree_and_reports_failure(tmp_path: Path) -> None:
    ok, _ = Verifier(test="test -f present.txt").verify(tmp_path)
    assert not ok
    (tmp_path / "present.txt").write_text("", encoding="utf-8")
    ok, output = Verifier(test="test -f present.txt && echo fine").verify(tmp_path)
    assert ok and "fine" in output


def test_verification_runs_without_github_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # It runs code the agent wrote, so it gets the agent's environment, not the manager's.
    monkeypatch.setenv("GH_TOKEN", "secret")
    # A Python script, so the check is the same under bash and under Windows' cmd.
    (tmp_path / "check.py").write_text(
        "import os, sys\nprint('clean')\nsys.exit(1 if os.environ.get('GH_TOKEN') else 0)\n",
        encoding="utf-8",
    )
    ok, output = Verifier(test=f'"{sys.executable}" check.py').verify(tmp_path)
    assert ok and "clean" in output


def test_the_state_store_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "state" / "state.json"
    store = JsonStateStore(path)
    assert store.manager_id("office-pc").startswith("office-pc-")
    store.set_poll_state("o/r", PollState(watched_since=T0, issues_since=T0, issues_etag='"e"'))
    store.sessions("o/r")[(42, "codex")] = "thread-1"
    store.set_limited("openai", T0)

    again = JsonStateStore(path)
    assert again.manager_id("office-pc") == store.manager_id("office-pc")
    assert again.poll_state("o/r") == PollState(
        watched_since=T0, issues_since=T0, issues_etag='"e"'
    )
    assert again.sessions("o/r")[(42, "codex")] == "thread-1"
    assert again.limited_until("openai") == T0
    assert again.poll_state("x/y") == PollState()
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]


def test_the_last_model_each_worker_ran_on_is_remembered(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    assert store.model("codex") is None
    store.set_model("codex", "gpt-6.1-sol")
    assert JsonStateStore(tmp_path / "state.json").model("codex") == "gpt-6.1-sol"


def test_given_a_pause_when_the_state_is_reloaded_then_it_is_still_paused(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    assert JsonStateStore(path).paused() is False
    JsonStateStore(path).set_paused(True)
    assert JsonStateStore(path).paused() is True
    JsonStateStore(path).set_paused(False)
    assert JsonStateStore(path).paused() is False


def _venv(root: Path) -> Path:
    scripts = root / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    return scripts


@pytest.mark.parametrize("name", [".venv", "venv"])
def test_given_a_virtual_environment_in_the_worktree_when_verified_then_it_is_active(
    tmp_path: Path, name: str
) -> None:
    scripts = _venv(tmp_path / name)
    (tmp_path / "check.py").write_text(
        "import os\nprint(os.environ['VIRTUAL_ENV'])\n"
        "print(os.environ['PATH'].split(os.pathsep)[0])\n",
        encoding="utf-8",
    )
    ok, output = Verifier(test=f'"{sys.executable}" check.py').verify(tmp_path)
    assert ok
    assert str(tmp_path / name) in output and str(scripts) in output


def test_given_a_folder_named_venv_without_pyvenv_cfg_when_verified_then_nothing_is_activated(
    tmp_path: Path,
) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / "check.py").write_text(
        "import os\nprint('active' if os.environ.get('VIRTUAL_ENV') else 'none')\n",
        encoding="utf-8",
    )
    ok, output = Verifier(test=f'"{sys.executable}" check.py').verify(tmp_path)
    assert ok and "none" in output
