"""F10.1 — the repository-root launchers ``run.sh`` and ``run.bat``.

The launchers are driven with fake interpreters: a fake ``python`` answers the version probe,
"creates" a virtual environment and "installs" a fake ``github-issue-worker`` that records its
arguments, so every test stays offline and in the fast tier.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = REPO_ROOT / "run.sh"
RUN_BAT = REPO_ROOT / "run.bat"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="run.sh is driven on POSIX")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="run.bat needs cmd.exe")


def test_given_the_launchers_when_read_then_they_build_a_venv_and_run_watch() -> None:
    sh = RUN_SH.read_text(encoding="utf-8")
    bat = RUN_BAT.read_text(encoding="utf-8")
    for text in (sh, bat):
        assert "-m venv" in text
        assert "pip install" in text
        assert "(3, 13)" in text
    assert 'github-issue-worker" watch "$@"' in sh
    assert "github-issue-worker" in bat and "watch %*" in bat
    assert "pause" in bat


def test_given_run_bat_when_read_as_bytes_then_every_line_ends_in_crlf() -> None:
    # cmd.exe resolves `goto`/`call :label` wrongly in LF-only files and can land in the
    # wrong branch; .gitattributes pins *.bat to CRLF.
    data = RUN_BAT.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n") > 0


def test_given_run_sh_when_read_as_bytes_then_no_line_ends_in_crlf() -> None:
    assert b"\r" not in RUN_SH.read_bytes()


# ---------------------------------------------------------------------------------------------
# run.sh


def _sh_executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _sh_fake_python(path: Path, status: str, version: str) -> Path:
    return _sh_executable(
        path,
        'case "$1 $2" in\n'
        f'  "-c "*) printf \'{status}|{version}|%s\\n\' "$0" ;;\n'
        '  "-m venv") mkdir -p "$3/bin"; cp "$FAKE_DIR/python3" "$3/bin/python" ;;\n'
        '  "-m pip") cp "$FAKE_DIR/worker" "$(dirname "$0")/github-issue-worker" ;;\n'
        "esac\n",
    )


def _sh_world(tmp_path: Path, python_version: str | None) -> dict[str, str]:
    """A checkout with run.sh, a PATH of basic tools only, and optionally a fake python3."""
    shutil.copy2(RUN_SH, tmp_path / "run.sh")
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    _sh_executable(fakes / "worker", "printf '%s\\n' \"$@\" > invoked.txt\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "rm", "mkdir", "cp", "chmod", "cat", "touch"):
        found = shutil.which(name)
        assert found, name
        (tools / name).symlink_to(found)
    if python_version is not None:
        old = python_version < "3.13"
        _sh_fake_python(fakes / "python3", "old" if old else "ok", python_version)
    return {"PATH": f"{fakes}{os.pathsep}{tools}", "FAKE_DIR": str(fakes), "HOME": str(tmp_path)}


def _run_sh(tmp_path: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(tmp_path / "tools" / "bash"), str(tmp_path / "run.sh"), *args],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=30,
    )


@posix_only
def test_given_a_fresh_posix_checkout_when_run_sh_starts_then_it_builds_installs_and_watches(
    tmp_path: Path,
) -> None:
    env = _sh_world(tmp_path, "3.13.0")

    result = _run_sh(tmp_path, env, "--once", "--no-ui")

    assert result.returncode == 0, result.stdout + result.stderr
    invoked = (tmp_path / "invoked.txt").read_text(encoding="utf-8").splitlines()
    assert invoked == ["watch", "--once", "--no-ui"]
    out = result.stdout
    assert "Python 3.13.0" in out
    assert "-m venv" in out and "-m pip install" in out
    assert "> github-issue-worker watch --once --no-ui" in out


@posix_only
def test_given_the_python_env_var_when_run_sh_starts_then_it_is_probed_first(
    tmp_path: Path,
) -> None:
    env = _sh_world(tmp_path, None)
    chosen = _sh_fake_python(tmp_path / "chosen-python", "ok", "3.14.1")
    shutil.copy2(chosen, tmp_path / "fakes" / "python3")

    result = _run_sh(tmp_path, {**env, "PYTHON": str(chosen)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Python 3.14.1" in result.stdout


@posix_only
def test_given_a_venv_built_with_python_3_12_when_run_sh_starts_then_it_rebuilds_it(
    tmp_path: Path,
) -> None:
    env = _sh_world(tmp_path, "3.13.0")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _sh_fake_python(venv_bin / "python", "old", "3.12.9")
    _sh_executable(venv_bin / "github-issue-worker", "touch stale-ran\n")

    result = _run_sh(tmp_path, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3.12.9" in result.stdout and "rebuilding" in result.stdout
    assert not (tmp_path / "stale-ran").exists()
    assert (tmp_path / "invoked.txt").read_text(encoding="utf-8").splitlines() == ["watch"]


@posix_only
@pytest.mark.parametrize("version", [None, "3.12.9"])
def test_given_no_python_3_13_when_run_sh_starts_then_it_fails_with_a_hint(
    tmp_path: Path, version: str | None
) -> None:
    env = _sh_world(tmp_path, version)

    result = _run_sh(tmp_path, env)

    assert result.returncode != 0
    assert "no Python 3.13 or newer" in result.stdout + result.stderr
    if version is not None:
        assert version in result.stdout
    assert not (tmp_path / ".venv").exists()


# ---------------------------------------------------------------------------------------------
# run.bat


def _bat_fake_python(path: Path, status: str, version: str) -> Path:
    path.write_text(
        "@echo off\n"
        'if "%~1"=="-c" goto probe\n'
        'if "%~2"=="venv" goto venv\n'
        'if "%~2"=="pip" goto pip\n'
        "exit /b 9\n"
        ":probe\n"
        f"echo {status}^|{version}^|%~f0\n"
        "exit /b 0\n"
        ":venv\n"
        'mkdir "%~3\\Scripts"\n'
        'copy /y "%FAKE_DIR%\\python.cmd" "%~3\\Scripts\\python.cmd" >nul\n'
        "exit /b 0\n"
        ":pip\n"
        'copy /y "%FAKE_DIR%\\worker.cmd" "%~dp0github-issue-worker.cmd" >nul\n'
        "exit /b 0\n",
        encoding="utf-8",
    )
    return path


def _bat_world(tmp_path: Path, python_version: str | None) -> dict[str, str]:
    """A checkout with run.bat, a PATH of System32 only, and optionally a fake python."""
    shutil.copy2(RUN_BAT, tmp_path / "run.bat")
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "worker.cmd").write_text("@echo off\necho %*> invoked.txt\n", encoding="utf-8")
    if python_version is not None:
        old = python_version < "3.13"
        _bat_fake_python(fakes / "python.cmd", "old" if old else "ok", python_version)
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env = {k: v for k, v in os.environ.items() if k.upper() not in {"PATH", "PYTHON"}}
    return {**env, "PATH": f"{fakes};{system_root}\\System32", "FAKE_DIR": str(fakes)}


def _run_bat(tmp_path: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        " ".join([f'"{tmp_path / "run.bat"}"', *args]),
        cwd=tmp_path,
        env=env,
        shell=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=30,
    )


@windows_only
def test_given_an_installed_windows_checkout_when_run_bat_starts_then_it_runs_watch(
    tmp_path: Path,
) -> None:
    env = _bat_world(tmp_path, None)
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(tmp_path / "fakes" / "worker.cmd", scripts / "github-issue-worker.cmd")

    result = _run_bat(tmp_path, env, "--once", "--no-ui")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "watch --once --no-ui" in (tmp_path / "invoked.txt").read_text(encoding="utf-8")
    assert "> github-issue-worker watch --once --no-ui" in result.stdout


@windows_only
def test_given_a_fresh_windows_checkout_when_run_bat_starts_then_it_builds_installs_and_watches(
    tmp_path: Path,
) -> None:
    env = _bat_world(tmp_path, "3.13.0")

    result = _run_bat(tmp_path, env, "--once")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "watch --once" in (tmp_path / "invoked.txt").read_text(encoding="utf-8")
    out = result.stdout
    assert "Python 3.13.0" in out
    assert "-m venv" in out and "-m pip install" in out


@windows_only
def test_given_a_venv_built_with_python_3_12_when_run_bat_starts_then_it_rebuilds_it(
    tmp_path: Path,
) -> None:
    env = _bat_world(tmp_path, "3.13.0")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    _bat_fake_python(scripts / "python.cmd", "old", "3.12.9")
    (scripts / "github-issue-worker.cmd").write_text(
        "@echo off\necho stale> stale-ran\n", encoding="utf-8"
    )

    result = _run_bat(tmp_path, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3.12.9" in result.stdout and "rebuilding" in result.stdout
    assert not (tmp_path / "stale-ran").exists()
    assert "watch" in (tmp_path / "invoked.txt").read_text(encoding="utf-8")


@windows_only
@pytest.mark.parametrize("version", [None, "3.12.9"])
def test_given_no_python_3_13_when_run_bat_starts_then_it_fails_with_a_hint(
    tmp_path: Path, version: str | None
) -> None:
    env = _bat_world(tmp_path, version)

    result = _run_bat(tmp_path, env)

    assert result.returncode != 0
    assert "no Python 3.13 or newer" in result.stdout
    if version is not None:
        assert version in result.stdout
    assert not (tmp_path / ".venv").exists()


@windows_only
def test_given_a_failing_watch_when_run_bat_ends_then_it_passes_the_exit_code_on(
    tmp_path: Path,
) -> None:
    env = _bat_world(tmp_path, None)
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "github-issue-worker.cmd").write_text("@echo off\nexit /b 7\n", encoding="utf-8")

    result = _run_bat(tmp_path, env)

    assert result.returncode == 7
    assert "exited with code 7" in result.stdout
