"""Starting an agent process: scrubbed environment, prompt on stdin, hard timeout (F6.4, F6.7)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass

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


def agent_environment(extra: dict[str, str], empty_gh_config: str) -> dict[str, str]:
    """The run's environment. ``GH_CONFIG_DIR`` points at an empty directory: merely unsetting it
    lets gh fall back to ``~/.config/gh``, which a live Codex run then used successfully."""
    env = {name: value for name, value in os.environ.items() if name not in SCRUBBED}
    return {**env, **_BLOCKED, "GH_CONFIG_DIR": empty_gh_config, **extra}


def run_invocation(invocation: Invocation, timeout: float) -> ProcessResult:
    """Run to completion or kill at ``timeout`` seconds. stdin always gets the prompt, then EOF."""
    with tempfile.TemporaryDirectory(prefix="ghiw-no-gh-") as empty_gh_config:
        return _run(invocation, timeout, empty_gh_config)


def _run(invocation: Invocation, timeout: float, empty_gh_config: str) -> ProcessResult:
    process = subprocess.Popen(
        invocation.argv,
        cwd=invocation.cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=agent_environment(dict(invocation.env), empty_gh_config),
        start_new_session=True,
    )
    with _LIVE_LOCK:
        _LIVE.add(process)
    try:
        stdout, stderr = process.communicate(invocation.stdin, timeout=timeout)
        return ProcessResult(process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        stdout, stderr = process.communicate()
        return ProcessResult(process.returncode or -9, stdout, stderr, timed_out=True)
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(process)


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kill the agent and everything it started (agents spawn shells and tools)."""
    try:
        os.killpg(process.pid, 9)
    except (ProcessLookupError, PermissionError, AttributeError):
        process.kill()
