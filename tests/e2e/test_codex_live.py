"""Real Codex through the adapter and the process runner.

Runs only where ``GHIW_CODEX_EXECUTABLE`` names a logged-in Codex (and ``GHIW_CODEX_HOME`` its
``CODEX_HOME``, for portable installations). It spends a few thousand tokens.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.adapters.process import run_invocation
from github_issue_worker.ports.agents import AgentRun

EXECUTABLE = os.environ.get("GHIW_CODEX_EXECUTABLE")
pytestmark = [
    pytest.mark.external_interop,
    pytest.mark.skipif(not EXECUTABLE, reason="GHIW_CODEX_EXECUTABLE is not set"),
]


def test_codex_works_a_task_and_honours_the_outcome_contract(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    adapter = CodexAdapter(executable=EXECUTABLE or "codex")
    run = AgentRun(
        worktree=tmp_path,
        prompt=(
            "Create a file greeting.txt containing exactly the word hello. Then also try to run "
            "`gh auth status` and report in the summary whether it worked."
        ),
    )
    invocation = adapter.invocation(run)
    home = os.environ.get("GHIW_CODEX_HOME")
    invocation = type(invocation)(
        argv=invocation.argv,
        cwd=invocation.cwd,
        stdin=invocation.stdin,
        env={"CODEX_HOME": home} if home else {},
    )

    result = run_invocation(invocation, timeout=600)
    report = adapter.report(run, result.exit_code, result.stdout, result.stderr)

    assert report.error is None, report.error
    assert report.session
    assert report.outcome is not None and report.outcome.kind == "done"
    assert (tmp_path / "greeting.txt").read_text(encoding="utf-8").strip() == "hello"
