"""Starting an agent process: scrubbed environment, prompt on stdin, hard timeout (F6.4, F6.7)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from github_issue_worker.ports.agents import Invocation

SCRUBBED = (
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GH_CONFIG_DIR",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
)
"""Credentials that would let an agent reach GitHub on its own. Runs never ask for permission, so
the environment is the boundary."""

_BLOCKED = {
    "GIT_SSH_COMMAND": "false",  # any ssh push fails
    "GIT_TERMINAL_PROMPT": "0",  # and an https one cannot ask for a password
}


_LIVE: set[subprocess.Popen[str]] = set()
_NAMED: dict[str, subprocess.Popen[str]] = {}
_KILLED: set[int] = set()
_LIVE_LOCK = threading.Lock()


def terminate_all() -> None:
    """Kill every agent still running, with everything it started (on manager shutdown)."""
    with _LIVE_LOCK:
        for process in list(_LIVE):
            _kill_group(process)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    killed: bool = False
    """Stopped from outside with `terminate`."""


def agent_environment(extra: dict[str, str], empty_gh_config: str) -> dict[str, str]:
    """The run's environment. ``GH_CONFIG_DIR`` points at an empty directory: merely unsetting it
    lets gh fall back to ``~/.config/gh``, which a live Codex run then used successfully."""
    env = {name: value for name, value in os.environ.items() if name not in SCRUBBED}
    return {**env, **_BLOCKED, "GH_CONFIG_DIR": empty_gh_config, **extra}


def run_invocation(
    invocation: Invocation, timeout: float, log: Path | None = None, name: str | None = None
) -> ProcessResult:
    """Run to completion or kill at ``timeout`` seconds. stdin always gets the prompt, then EOF.

    stdout goes to ``log`` as it is written, so the run can be watched while it is going; ``name``
    lets `terminate` kill this run from outside (the UI's stop).
    """
    with (
        tempfile.TemporaryDirectory(prefix="ghiw-no-gh-") as empty_gh_config,
        tempfile.TemporaryDirectory(prefix="ghiw-run-") as scratch,
    ):
        target = log or Path(scratch) / "stdout"
        target.parent.mkdir(parents=True, exist_ok=True)
        return _run(invocation, timeout, empty_gh_config, target, name)


def terminate(name: str) -> bool:
    """Kill the named run with everything it started. False when nothing by that name runs."""
    with _LIVE_LOCK:
        process = _NAMED.get(name)
        if process is None:
            return False
        _KILLED.add(process.pid)
    _kill_group(process)
    return True


def _run(
    invocation: Invocation, timeout: float, empty_gh_config: str, log: Path, name: str | None
) -> ProcessResult:
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            invocation.argv,
            cwd=invocation.cwd,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=agent_environment(dict(invocation.env), empty_gh_config),
            start_new_session=True,
        )
        with _LIVE_LOCK:
            _LIVE.add(process)
            if name:
                _NAMED[name] = process
        timed_out = False
        try:
            try:
                _, stderr = process.communicate(invocation.stdin, timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(process)
                _, stderr = process.communicate()
                timed_out = True
        finally:
            with _LIVE_LOCK:
                _LIVE.discard(process)
                if name and _NAMED.get(name) is process:
                    del _NAMED[name]
                killed = process.pid in _KILLED
                _KILLED.discard(process.pid)
    stdout = log.read_text(encoding="utf-8", errors="replace")
    code = process.returncode if process.returncode is not None else -9
    if timed_out:
        code = code or -9
    return ProcessResult(code, stdout, stderr, timed_out=timed_out, killed=killed)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kill the agent and everything it started (agents spawn shells and tools)."""
    try:
        os.killpg(process.pid, 9)
    except (ProcessLookupError, PermissionError, AttributeError):
        process.kill()
