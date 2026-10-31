from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import github_issue_worker.wiring as wiring
from github_issue_worker.adapters.agents.claude import ClaudeAdapter
from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.adapters.process import ProcessResult
from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.application.issue_run import RunStopped
from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.configuration import Configuration
from github_issue_worker.ports.agents import AgentAdapter, AgentRun, Invocation, RunReport
from github_issue_worker.wiring import Runtime, log_dir
from tests.application.run_fakes import FakeWorkspace
from tests.fakes import FakeClock


@pytest.fixture
def runtime(tmp_path: Path) -> Runtime:
    return Runtime(
        config=Configuration(),
        home=tmp_path,
        api=SimpleNamespace(clock=FakeClock(datetime(2026, 10, 31, tzinfo=UTC))),  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state" / "state.json"),
        state_path=tmp_path / "state" / "state.json",
        manager_id="office",
        workspace=FakeWorkspace(tmp_path / "cache"),  # type: ignore[arg-type]
    )


def output(cost: float | None = 0.125) -> str:
    event: dict[str, object] = {
        "type": "result",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "structured_output": {"outcome": "done", "summary": "Finished"},
    }
    if cost is not None:
        event["total_cost_usd"] = cost
    return json.dumps(event) + "\n"


def execute_result(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    result: ProcessResult,
    *,
    repository: str = "owner/project",
    issue: int = 73,
    worker: str = "worker.1",
    adapter: AgentAdapter | None = None,
) -> tuple[Path, RunReport]:
    slot = WorkerSlot(name=worker, agent="claude", account="account")
    path = runtime._new_log(slot, repository, issue)
    tree = runtime.home / "cache" / "worktree"
    tree.mkdir(parents=True, exist_ok=True)

    def process(
        invocation: Invocation, timeout: float, log: Path | None = None, name: str | None = None
    ) -> ProcessResult:
        assert log == path and name == worker
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout, encoding="utf-8")
        return result

    monkeypatch.setattr(wiring, "run_invocation", process)
    report = wiring.execute(
        adapter or ClaudeAdapter("unused"), AgentRun(tree, "Work on this issue"), 10, path, worker
    )
    return path, report


def metadata(path: Path) -> dict[str, object]:
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_reported_cost_and_worker_are_kept_with_the_issues_transcript(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = execute_result(runtime, monkeypatch, ProcessResult(0, output(), ""))
    assert path.parent == log_dir(runtime.state_path.parent, "owner/project", 73)
    assert path.read_text(encoding="utf-8") == output()
    assert metadata(path) == {
        "transcript": path.name,
        "worker": "worker.1",
        "cost_usd": 0.125,
        "stderr": "",
    }


@pytest.mark.parametrize("cost", [None, 0.0])
def test_unknown_and_zero_reported_costs_remain_distinct(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, cost: float | None
) -> None:
    path, _ = execute_result(runtime, monkeypatch, ProcessResult(0, output(cost), ""))
    assert metadata(path)["cost_usd"] == cost


def test_token_only_usage_does_not_invent_a_cost(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
    path, report = execute_result(
        runtime, monkeypatch, ProcessResult(0, text, ""), adapter=CodexAdapter()
    )
    assert report.usage is not None
    assert metadata(path)["cost_usd"] is None


def test_retries_and_continuations_within_one_second_keep_separate_records(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = []
    for cost in (0.125, 0.25, None):
        path, _ = execute_result(runtime, monkeypatch, ProcessResult(0, output(cost), ""))
        paths.append(path)
    assert len(set(paths)) == 3
    for path, cost in zip(paths, (0.125, 0.25, None), strict=True):
        assert path.read_text(encoding="utf-8") == output(cost)
        assert metadata(path)["cost_usd"] == cost


def test_different_issues_repositories_and_workers_keep_their_own_data(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = [
        ("owner/project", 73, "worker.1", 0.125),
        ("owner/project", 74, "worker.1", None),
        ("other/project", 73, "worker.2", 0.5),
    ]
    paths = []
    for repository, issue, worker, cost in cases:
        path, _ = execute_result(
            runtime,
            monkeypatch,
            ProcessResult(0, output(cost), ""),
            repository=repository,
            issue=issue,
            worker=worker,
        )
        paths.append(path)
    for path, (repository, issue, worker, cost) in zip(paths, cases, strict=True):
        assert path.parent == log_dir(runtime.state_path.parent, repository, issue)
        assert metadata(path)["worker"] == worker
        assert metadata(path)["cost_usd"] == cost
        assert path.read_text(encoding="utf-8") == output(cost)


@pytest.mark.parametrize("ending", ["failure", "limit", "timeout", "stop"])
def test_unsuccessful_runs_keep_available_output_worker_and_reported_cost(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    text = output()
    if ending in {"limit", "timeout"}:
        text += '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}\n'
    result = ProcessResult(
        1, text, "diagnostic output", timed_out=ending == "timeout", killed=ending == "stop"
    )
    if ending == "stop":
        with pytest.raises(RunStopped):
            execute_result(runtime, monkeypatch, result)
        (path,) = log_dir(runtime.state_path.parent, "owner/project", 73).glob("*.ndjson")
    else:
        path, report = execute_result(runtime, monkeypatch, result)
        if ending == "limit":
            assert report.limit is not None
        else:
            assert report.error is not None
        if ending == "timeout":
            assert report.limit is None
            assert report.outcome is None
            assert report.error == "timed out after 10 s"
    assert path.read_text(encoding="utf-8") == text
    assert metadata(path)["worker"] == "worker.1"
    assert metadata(path)["cost_usd"] == 0.125
    assert metadata(path)["stderr"] == "diagnostic output"


def test_local_run_logs_survive_restart_and_worktree_cleanup(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = execute_result(runtime, monkeypatch, ProcessResult(0, output(), ""))
    shutil.rmtree(runtime.home / "cache")
    restarted = Runtime(
        config=runtime.config,
        home=runtime.home,
        api=runtime.api,
        state=JsonStateStore(runtime.state_path),
        state_path=runtime.state_path,
        manager_id="office",
        workspace=runtime.workspace,
    )
    (retained,) = log_dir(restarted.state_path.parent, "owner/project", 73).glob("*.ndjson")
    assert retained == path
    assert retained.read_text(encoding="utf-8") == output()
    assert metadata(retained)["worker"] == "worker.1"
    assert metadata(retained)["cost_usd"] == 0.125
