from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import github_issue_worker.wiring as wiring
from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.application.manager import RepositoryWatch, WorkerSlot
from github_issue_worker.configuration import RepositorySettings, WorkerSettings, parse
from github_issue_worker.domain.claim import ClaimPolicy, Holder
from github_issue_worker.domain.commit_message import DEFAULT_FORBIDDEN_WORDS, CommitGate, Rule
from github_issue_worker.domain.issue import Actor, Issue
from github_issue_worker.domain.outcome import Outcome
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentRun, Invocation, RunReport
from github_issue_worker.wiring import Runtime, make_adapter, repository_run
from tests.application.run_fakes import FakeHost, FakeWorkspace
from tests.fakes import FakeClock, InMemoryClaimStore


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


class _DummyAdapter:
    def invocation(self, run: AgentRun) -> Invocation:
        return Invocation(argv=("true",), cwd=run.worktree)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        return RunReport(session="s1", outcome=Outcome("done", "Done."))


def _run_commit_through_wired_issue_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_data: dict[str, object],
    repository: str,
    commit_message: str,
) -> tuple[str, list[str]]:
    config = parse(json.dumps(config_data))
    repo_settings = next(
        (r for r in config.repositories if r.repository == repository),
        RepositorySettings(repository=repository),
    )
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=UTC))
    store = InMemoryClaimStore()
    coordinator = Coordinator(store, clock, ClaimPolicy(timedelta(days=7), timedelta(days=1)))
    issue = Issue(
        9,
        "Configurable commit gate",
        "Allow per-repository forbiddenWords.",
        "open",
        Actor("Hawkynt"),
        frozenset(),
        False,
        clock.now(),
        clock.now(),
    )
    host = FakeHost(repository=repository, issues={9: issue})
    workspace = FakeWorkspace(tmp_path)
    workspace.head_listener = lambda branch, sha: host.head_of.__setitem__(branch, sha)
    watch = RepositoryWatch(
        name=repository,
        host=host,
        coordinator=coordinator,
        store=store,
        settings=repository_run(config, repo_settings),
    )
    slot = WorkerSlot(name="w", agent="codex", account="a")
    handle = coordinator.claim(9, Holder("office-pc", "w"), branch="issue/9-gate")
    assert handle is not None

    prompts: list[str] = []

    def fake_execute(adapter: object, run: AgentRun, timeout: float) -> RunReport:
        if "## This run: understand the issue" in run.prompt:
            # The understanding run comes first and changes nothing (F4.5).
            return RunReport(
                session="s1", outcome=Outcome("done", "Understood.", restatement="Restated.")
            )
        prompts.append(run.prompt)
        workspace.trees[9] = []
        if len(prompts) == 1:
            workspace.add_commit(9, commit_message)
        else:
            workspace.add_commit(9, "+ clean subject\n\nClean body without forbidden terms.")
        return RunReport(
            session="s1",
            outcome=Outcome(
                "done",
                "Done.",
                scenarios=("Given a commit When checked Then gated",),
                labels=("enhancement",),
            ),
        )

    monkeypatch.setattr(wiring, "execute", fake_execute)
    runtime = Runtime(
        config=config,
        home=tmp_path,
        api=None,  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="office-pc",
        workspace=workspace,  # type: ignore[arg-type]
        watches={repository: watch},
        adapters={"w": _DummyAdapter()},
        slots=[slot],
    )
    object.__setattr__(runtime, "api", type("_Api", (), {"clock": clock})())
    verdict = runtime.run_issue(watch, slot, handle, issue, (), RunKind.FIRST)
    return verdict.kind, prompts


def test_leaving_forbidden_words_out_builds_the_commit_gate_with_default_forbidden_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = parse('{"repositories": [{"repository": "Hawkynt/ShiftDiff"}]}')
    run = repository_run(config, config.repositories[0])
    assert run.forbidden_words == DEFAULT_FORBIDDEN_WORDS
    assert run.gate == CommitGate(forbidden_words=DEFAULT_FORBIDDEN_WORDS)

    verdict, prompts = _run_commit_through_wired_issue_run(
        tmp_path,
        monkeypatch,
        {"repositories": [{"repository": "Hawkynt/ShiftDiff"}]},
        "Hawkynt/ShiftDiff",
        "+ add claude adapter\n\nWire the claude command line.",
    )
    assert verdict == "merged"
    assert len(prompts) == 2
    assert "ai-trace" in prompts[1]


def test_defaults_forbidden_words_builds_the_commit_gate_for_repositories_without_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw: dict[str, object] = {
        "defaults": {"forbiddenWords": ["customtrace"]},
        "repositories": [{"repository": "Hawkynt/ShiftDiff"}],
    }
    config = parse(json.dumps(raw))
    run = repository_run(config, config.repositories[0])
    assert run.forbidden_words == ("customtrace",)
    assert run.gate == CommitGate(forbidden_words=("customtrace",))

    verdict, prompts = _run_commit_through_wired_issue_run(
        tmp_path,
        monkeypatch,
        raw,
        "Hawkynt/ShiftDiff",
        "+ add claude adapter\n\nWire the claude command line.",
    )
    assert verdict == "merged"
    assert len(prompts) == 1

    verdict_blocked, prompts_blocked = _run_commit_through_wired_issue_run(
        tmp_path / "blocked",
        monkeypatch,
        raw,
        "Hawkynt/ShiftDiff",
        "+ add customtrace support\n\nMention customtrace in the body.",
    )
    assert verdict_blocked == "merged"
    assert len(prompts_blocked) == 2
    assert "customtrace" in prompts_blocked[1]


def test_per_repository_forbidden_words_overrides_defaults_when_building_the_commit_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw: dict[str, object] = {
        "defaults": {"forbiddenWords": ["claude", "agents?"]},
        "repositories": [
            {"repository": "Hawkynt/ShiftDiff"},
            {"repository": "Hawkynt/GitHubIssueWorker", "forbiddenWords": []},
        ],
    }
    config = parse(json.dumps(raw))
    default_repo_run = repository_run(config, config.repositories[0])
    override_repo_run = repository_run(config, config.repositories[1])
    assert default_repo_run.forbidden_words == ("claude", "agents?")
    assert override_repo_run.forbidden_words == ()
    assert override_repo_run.gate == CommitGate(forbidden_words=())

    verdict, prompts = _run_commit_through_wired_issue_run(
        tmp_path,
        monkeypatch,
        raw,
        "Hawkynt/GitHubIssueWorker",
        "+ add claude agent adapter\n\nWire the claude and codex agents.",
    )
    assert verdict == "merged"
    assert len(prompts) == 1


@pytest.mark.parametrize(
    "trace_line",
    [
        "An AI wrote this.",
        "Generated with a tool.",
        "🤖 automated commit.",
    ],
)
def test_always_forbidden_traces_stay_forbidden_in_a_wired_repository_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace_line: str
) -> None:
    raw: dict[str, object] = {
        "defaults": {"forbiddenWords": []},
        "repositories": [{"repository": "Hawkynt/GitHubIssueWorker", "forbiddenWords": []}],
    }
    config = parse(json.dumps(raw))
    run = repository_run(config, config.repositories[0])
    assert {v.rule for v in run.gate.check(f"+ add feature\n\n{trace_line}")} == {Rule.AI_TRACE}

    verdict, prompts = _run_commit_through_wired_issue_run(
        tmp_path,
        monkeypatch,
        raw,
        "Hawkynt/GitHubIssueWorker",
        f"+ add feature\n\n{trace_line}",
    )
    assert verdict == "merged"
    assert len(prompts) == 2
    assert "ai-trace" in prompts[1]
