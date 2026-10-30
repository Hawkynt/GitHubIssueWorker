from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from github_issue_worker.adapters.process import SCRUBBED, run_invocation
from github_issue_worker.ports.agents import Invocation

PY = sys.executable


def test_stdout_stderr_and_exit_code_come_back(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    result = run_invocation(Invocation(argv=[PY, "-c", code], cwd=tmp_path), timeout=10)
    assert (result.exit_code, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")
    assert not result.timed_out


def test_the_prompt_arrives_on_stdin_and_stdin_is_then_closed(tmp_path: Path) -> None:
    code = "import sys; print(sys.stdin.read().upper())"
    invocation = Invocation(argv=[PY, "-c", code], cwd=tmp_path, stdin="fix issue 42")
    assert run_invocation(invocation, timeout=10).stdout.strip() == "FIX ISSUE 42"


def test_it_runs_in_the_worktree(tmp_path: Path) -> None:
    invocation = Invocation(argv=[PY, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert Path(run_invocation(invocation, timeout=10).stdout.strip()) == tmp_path


def test_outward_credentials_are_scrubbed_and_ssh_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in SCRUBBED:
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("KEEP_ME", "yes")
    code = (
        "import os, json; d = os.environ['GH_CONFIG_DIR'];"
        " print(json.dumps({**os.environ, 'seen': [os.path.isdir(d), os.listdir(d)]}))"
    )
    invocation = Invocation(argv=[PY, "-c", code], cwd=tmp_path, env={"CODEX_HOME": "/x"})
    env = json.loads(run_invocation(invocation, timeout=10).stdout)
    assert not (set(SCRUBBED) - {"GH_CONFIG_DIR"}) & set(env)
    assert {"GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "SSH_AUTH_SOCK"} <= set(SCRUBBED)
    assert env["GIT_SSH_COMMAND"] == "false"
    # Unsetting GH_CONFIG_DIR is not enough: gh falls back to ~/.config/gh, which a live Codex run
    # used successfully. It must point at an empty directory instead.
    assert env["seen"] == [True, []]
    assert not Path(env["GH_CONFIG_DIR"]).exists()  # removed again after the run
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["KEEP_ME"] == "yes" and env["CODEX_HOME"] == "/x"


def test_a_run_past_its_timeout_is_killed(tmp_path: Path) -> None:
    invocation = Invocation(argv=[PY, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
    result = run_invocation(invocation, timeout=0.5)
    assert result.timed_out
    assert result.exit_code != 0
