"""F15.9 — an agent installation counts only once it answered ``--version``."""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

import pytest

from github_issue_worker.adapters import agent_version
from github_issue_worker.adapters.agent_version import version_of
from github_issue_worker.adapters.installations import Resolved, Unavailable

WINDOWS = sys.platform == "win32"


def fake_agent(directory: Path, name: str, posix: str, windows: str) -> Path:
    """A fake CLI: a shell script on POSIX, a batch file on Windows."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f"{name}.cmd" if WINDOWS else name)
    body = f"@echo off\r\n{windows}\r\n" if WINDOWS else f"#!/bin/sh\n{posix}\n"
    path.write_text(body, encoding="utf-8", newline="")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    agent_version._cache.clear()


def test_given_an_agent_when_asked_then_its_first_printed_line_is_the_version(
    tmp_path: Path,
) -> None:
    claude = fake_agent(
        tmp_path,
        "claude",
        'echo; echo "2.1.331 (Claude Code)"; echo "more"',
        "echo.\r\necho 2.1.331 (Claude Code)\r\necho more",
    )
    assert version_of(Resolved(claude)) == "2.1.331 (Claude Code)"


def test_given_the_installation_environment_when_asked_then_the_agent_runs_in_it(
    tmp_path: Path,
) -> None:
    agent = fake_agent(tmp_path, "agy", 'echo "$GHIW_PROBE"', "echo %GHIW_PROBE%")
    assert version_of(Resolved(agent, {"GHIW_PROBE": "1.1.1"})) == "1.1.1"


@pytest.mark.parametrize(
    ("posix", "windows", "reason"),
    [
        ('echo "boom" >&2; exit 3', "echo boom 1>&2\r\nexit /b 3", "boom"),
        ("exit 0", "exit /b 0", "printed no version"),
        ("exit 7", "exit /b 7", "exit code 7"),
    ],
)
def test_given_an_agent_that_does_not_answer_when_asked_then_it_is_unavailable(
    tmp_path: Path, posix: str, windows: str, reason: str
) -> None:
    agent = fake_agent(tmp_path, "codex", posix, windows)
    with pytest.raises(Unavailable, match=reason):
        version_of(Resolved(agent))


def test_given_a_missing_executable_when_asked_then_it_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(Unavailable, match="does not exist"):
        version_of(Resolved(tmp_path / "nowhere" / "claude"))


def test_given_an_agent_that_hangs_when_asked_then_it_is_given_up_in_time(tmp_path: Path) -> None:
    agent = fake_agent(tmp_path, "claude", "sleep 30", "ping -n 31 127.0.0.1 >nul")
    started = time.monotonic()
    with pytest.raises(Unavailable, match="did not answer"):
        version_of(Resolved(agent), timeout=0.5)
    assert time.monotonic() - started < 10


def test_given_an_unchanged_executable_when_asked_again_then_it_is_not_run_again(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "count"
    agent = fake_agent(
        tmp_path / "bin",
        "claude",
        f'echo x >> "{counter}"; echo 1.0.0',
        f'echo x>>"{counter}"\r\necho 1.0.0',
    )
    assert version_of(Resolved(agent)) == "1.0.0"
    assert version_of(Resolved(agent)) == "1.0.0"
    assert counter.read_text(encoding="utf-8").count("x") == 1


def test_given_an_updated_executable_when_asked_again_then_the_new_version_is_seen(
    tmp_path: Path,
) -> None:
    agent = fake_agent(tmp_path, "claude", "echo 1.0.0", "echo 1.0.0")
    assert version_of(Resolved(agent)) == "1.0.0"
    fake_agent(tmp_path, "claude", "echo 1.0.1 (updated)", "echo 1.0.1 (updated)")
    assert version_of(Resolved(agent)) == "1.0.1 (updated)"


def test_given_claudelocal_when_asked_then_its_status_proves_it(tmp_path: Path) -> None:
    launcher = fake_agent(
        tmp_path,
        "claude",
        'if [ "$1" = "--status" ]; then echo "gateway up, Qwen3.8"; else exit 9; fi',
        'if "%1"=="--status" (echo gateway up, Qwen3.8) else (exit /b 9)',
    )
    assert version_of(Resolved(launcher, local=True)) == "gateway up, Qwen3.8"
