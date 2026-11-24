"""Proves an agent installation exists by asking it for its version (F15.9).

A worker counts only when its installation answers: ``<agent> --version`` (ClaudeLocal's launcher:
``--status``), run with the installation's own environment, exactly as a run would start it. The
first line it prints is the version shown everywhere, so it is always visible which harness version
a worker runs. The answer is kept per executable file and its modification time and size: an agent
that updated itself is asked again, an unchanged one is not.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from github_issue_worker.adapters.installations import Resolved, Unavailable

TIMEOUT = 20.0
"""Seconds an agent gets to print its version; a cold npm-installed CLI needs a few."""

_cache: dict[tuple[str, int, int, bool], str] = {}
_lock = threading.Lock()


def version_of(resolved: Resolved, timeout: float = TIMEOUT) -> str:
    """The first line the installation prints when asked for its version.

    Raises `Unavailable`, saying why, when it does not exist, does not start, fails, prints
    nothing or does not answer within ``timeout`` seconds.
    """
    executable = resolved.executable
    try:
        info = executable.stat()
    except OSError:
        raise Unavailable(f"{executable} does not exist") from None
    key = (str(executable), info.st_mtime_ns, info.st_size, resolved.local)
    with _lock:
        if key in _cache:
            return _cache[key]
    question = "--status" if resolved.local else "--version"
    asked = f"{executable.name} {question}"
    try:
        process = subprocess.Popen(
            [str(executable), question],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **resolved.env},
            start_new_session=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as error:
        raise Unavailable(f"{executable} could not be started: {error}") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        process.communicate()
        raise Unavailable(f"{asked} did not answer within {timeout:g} s") from None

    line = next((text.strip() for text in stdout.splitlines() if text.strip()), "")
    if process.returncode != 0:
        said = [text.strip() for text in (stderr or stdout).splitlines() if text.strip()]
        detail = said[-1] if said else f"exit code {process.returncode}"
        raise Unavailable(f"{asked} failed: {detail}")
    if not line:
        raise Unavailable(f"{asked} printed no version")
    with _lock:
        _cache[key] = line
    return line


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill the agent and whatever it started; on Windows a batch file's child keeps the pipes."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            process.kill()
    if process.poll() is None:
        process.kill()
