from __future__ import annotations

import json
import stat
from pathlib import Path

from github_issue_worker.configuration import WorkerSettings, parse
from github_issue_worker.ports.agents import AgentRun
from github_issue_worker.wiring import make_adapter


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def claude_worker(installation: dict[str, str], budget: float | None) -> WorkerSettings:
    body: dict[str, object] = {
        "name": "w",
        "agent": "claude",
        "account": "a",
        "installation": installation,
    }
    if budget is not None:
        body["maxBudgetUsdPerRun"] = budget
    return parse(json.dumps({"accounts": {"a": {}}, "workers": [body]})).workers[0]


def argv(worker: WorkerSettings, tmp_path: Path) -> list[str]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    run = AgentRun(worktree=worktree, prompt="Fix issue 5.")
    return make_adapter(worker, tmp_path).invocation(run).argv


def test_a_claude_worker_s_budget_reaches_its_runs(tmp_path: Path) -> None:
    claude = executable(tmp_path / "bin" / "claude")
    worker = claude_worker({"type": "executable", "path": str(claude)}, 2.5)
    args = argv(worker, tmp_path)
    assert args[args.index("--max-budget-usd") + 1] == "2.5"


def test_a_claude_worker_without_a_budget_runs_without_one(tmp_path: Path) -> None:
    claude = executable(tmp_path / "bin" / "claude")
    worker = claude_worker({"type": "executable", "path": str(claude)}, None)
    assert "--max-budget-usd" not in argv(worker, tmp_path)


def test_a_claudelocal_worker_gets_no_budget_flag(tmp_path: Path) -> None:
    executable(tmp_path / "ClaudeLocal" / "claude.sh")
    executable(tmp_path / "ClaudeLocal" / "Claude.bat")
    worker = claude_worker({"type": "claudelocal", "root": str(tmp_path / "ClaudeLocal")}, 2.5)
    assert "--max-budget-usd" not in argv(worker, tmp_path)
