from __future__ import annotations

import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import github_issue_worker.wiring as wiring
from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.application.manager import RepositoryWatch, WorkerSlot
from github_issue_worker.configuration import (
    Configuration,
    RepositorySettings,
    WorkerSettings,
    parse,
)
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


def command_worker(
    installation: dict[str, object] | None = None, **extra: object
) -> WorkerSettings:
    body: dict[str, object] = {
        "name": "w",
        "agent": "command",
        "account": "a",
        "command": ["mycli", "--message", "{prompt}"],
        **extra,
    }
    if installation:
        body["installation"] = installation
    return parse(json.dumps({"accounts": {"a": {}}, "workers": [body]})).workers[0]


def test_a_command_worker_runs_its_template_with_the_program_found_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows finds a program on PATH only by a PATHEXT extension.
    mycli = executable(tmp_path / "bin" / ("mycli.cmd" if sys.platform == "win32" else "mycli"))
    monkeypatch.setenv("PATH", str(mycli.parent))
    worker = command_worker(limitExitCodes=[75], limitPatterns=["quota"])
    adapter = make_adapter(worker, tmp_path)
    assert adapter.kind == "command"
    program, *arguments = argv(worker, tmp_path)
    assert Path(program).name.lower() == mycli.name.lower()
    assert arguments == ["--message", "Fix issue 5."]
    run = AgentRun(worktree=tmp_path, prompt="x")
    assert adapter.report(run, 75, "", "").limit is not None
    assert adapter.report(run, 1, "", "quota gone").limit is not None


def test_a_command_worker_with_an_executable_installation_gets_its_path_and_env(
    tmp_path: Path,
) -> None:
    tool = executable(tmp_path / "opt" / "mycli-1.2")
    worker = command_worker({"type": "executable", "path": str(tool), "env": {"MYCLI_HOME": "/p"}})
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    invocation = make_adapter(worker, tmp_path).invocation(AgentRun(worktree, "Fix issue 5."))
    assert invocation.argv == [str(tool), "--message", "Fix issue 5."]
    assert invocation.env == {"MYCLI_HOME": "/p"}


def test_a_command_worker_whose_program_is_missing_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(wiring.Unavailable, match="mycli"):
        make_adapter(command_worker(), tmp_path)


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
    host.link_listener = workspace.remote.__setitem__
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

    def fake_execute(adapter: object, run: AgentRun, timeout: float, **_: object) -> RunReport:
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


def test_create_missing_labels_defaults_off_and_a_repository_override_turns_it_on() -> None:
    raw: dict[str, object] = {
        "repositories": [
            {"repository": "Hawkynt/ShiftDiff"},
            {"repository": "Hawkynt/GitHubIssueWorker", "createMissingLabels": True},
        ]
    }
    config = parse(json.dumps(raw))
    assert repository_run(config, config.repositories[0]).create_missing_labels is False
    assert repository_run(config, config.repositories[1]).create_missing_labels is True


def test_create_missing_labels_on_by_default_is_overridden_off_by_a_repository() -> None:
    raw: dict[str, object] = {
        "defaults": {"createMissingLabels": True},
        "repositories": [{"repository": "Hawkynt/ShiftDiff", "createMissingLabels": False}],
    }
    config = parse(json.dumps(raw))
    assert repository_run(config, config.repositories[0]).create_missing_labels is False


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


def test_a_changed_configuration_is_applied_without_a_restart(tmp_path: Path) -> None:
    # F0.7: workers and repositories added, changed and removed while the manager runs.
    claude = executable(tmp_path / "bin" / "claude")
    before = parse(
        json.dumps(
            {
                "accounts": {"a": {}},
                "workers": [
                    {
                        "name": "idle",
                        "agent": "claude",
                        "account": "a",
                        "installation": {"type": "executable", "path": str(claude)},
                    },
                    {
                        "name": "busy",
                        "agent": "claude",
                        "account": "a",
                        "installation": {"type": "executable", "path": str(claude)},
                    },
                    {
                        "name": "kept",
                        "agent": "claude",
                        "account": "a",
                        "priority": 5,
                        "installation": {"type": "executable", "path": str(claude)},
                    },
                ],
                "repositories": [{"repository": "o/gone"}, {"repository": "o/stays"}],
            }
        )
    )
    clock = FakeClock(datetime(2026, 10, 30, tzinfo=UTC))
    host = FakeHost()
    watches = {
        name: RepositoryWatch(
            name=name,
            host=host,
            coordinator=Coordinator(
                InMemoryClaimStore(), clock, ClaimPolicy(timedelta(days=7), timedelta(days=1))
            ),
            store=InMemoryClaimStore(),
        )
        for name in ("o/gone", "o/stays")
    }
    slots = [
        WorkerSlot(name=w.name, agent=w.agent, account=w.account, priority=w.priority)
        for w in before.workers
    ]
    slots[1].busy = True
    runtime = Runtime(
        config=before,
        home=tmp_path,
        api=None,  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="m",
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
        watches=dict(watches),
        slots=list(slots),
    )
    from github_issue_worker.application.manager import Manager

    manager = Manager(
        manager_id="m",
        manager_tags=frozenset(),
        repositories=list(watches.values()),
        workers=runtime.slots,
        poll=lambda *a: (None, None),  # type: ignore[arg-type,return-value]
        poll_states={},
        limits=runtime.state,
        clock=clock,
        trusted_users=(),
        run_issue=lambda *a: None,  # type: ignore[arg-type,return-value]
        spawn=lambda job: job(),
    )

    after = parse(
        json.dumps(
            {
                "trustedUsers": ["helper"],
                "accounts": {"a": {}},
                "workers": [
                    {
                        "name": "kept",
                        "agent": "claude",
                        "account": "a",
                        "priority": 1,
                        "model": "opus",
                        "installation": {"type": "executable", "path": str(claude)},
                    },
                    {
                        "name": "new",
                        "agent": "codex",
                        "account": "a",
                        "installation": {"type": "executable", "path": str(tmp_path / "missing")},
                    },
                ],
                "repositories": [{"repository": "o/stays", "mode": "labeled"}],
            }
        )
    )
    runtime.apply(after, manager)

    assert runtime.config is after
    assert manager.trusted_users == ("helper",)
    assert [w.name for w in manager.repositories] == ["o/stays"]
    assert manager.repositories[0].mode == "labeled"
    names = {slot.name: slot for slot in manager.workers}
    assert "idle" not in names  # removed at once
    assert not names["busy"].enabled  # finishes its run, then takes nothing new
    assert (names["kept"].priority, names["kept"].model) == (1, "opus")
    assert not names["new"].available and "does not exist" in names["new"].reason


def test_the_prose_limit_reaches_the_commit_gate() -> None:
    config = parse(
        json.dumps(
            {
                "defaults": {"maxProseLines": 0},
                "repositories": [{"repository": "o/a"}, {"repository": "o/b", "maxProseLines": 5}],
            }
        )
    )
    assert repository_run(config, config.repositories[0]).gate.max_prose_lines == 0
    assert repository_run(config, config.repositories[1]).gate.max_prose_lines == 5


def _instances_config(claude: Path, runs: int) -> Configuration:
    return parse(
        json.dumps(
            {
                "accounts": {"a": {"maxConcurrentRuns": runs}},
                "workers": [
                    {
                        "name": "claude",
                        "agent": "claude",
                        "account": "a",
                        "installation": {"type": "executable", "path": str(claude)},
                    }
                ],
            }
        )
    )


def test_a_worker_runs_as_many_instances_as_its_account_allows(tmp_path: Path) -> None:
    # F12.11: maxConcurrentRuns 3 gives claude, claude.2 and claude.3, one issue each.
    claude = executable(tmp_path / "bin" / "claude")
    runtime = Runtime(
        config=_instances_config(claude, 3),
        home=tmp_path,
        api=None,  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="m",
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
    )
    runtime.sync_slots()
    assert [(s.name, s.group) for s in runtime.slots] == [
        ("claude", "claude"),
        ("claude.2", "claude"),
        ("claude.3", "claude"),
    ]
    assert set(runtime.adapters) == {"claude"}  # one adapter serves every instance

    runtime.slots[1].busy = True
    runtime.config = _instances_config(claude, 1)
    runtime.sync_slots()
    names = {slot.name: slot for slot in runtime.slots}
    assert set(names) == {"claude", "claude.2"}  # claude.3 went at once
    assert not names["claude.2"].enabled  # finishes its run, then takes nothing new


def test_splitting_is_on_by_default_and_can_be_switched_off_per_repository() -> None:
    config = parse(
        json.dumps(
            {"repositories": [{"repository": "o/a"}, {"repository": "o/b", "splitIssues": False}]}
        )
    )
    assert repository_run(config, config.repositories[0]).split_issues is True
    assert repository_run(config, config.repositories[1]).split_issues is False
    off = parse(
        json.dumps({"defaults": {"splitIssues": False}, "repositories": [{"repository": "o/a"}]})
    )
    assert repository_run(off, off.repositories[0]).split_issues is False


class _Named:
    kind = "claude"

    def default_model(self) -> str | None:
        return "Qwen3.8-Flash-Next IQ3_S"


def test_a_worker_whose_runs_never_reported_a_model_uses_its_agents_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A local run that crashed before reporting was recorded as "unknown model".
    import github_issue_worker.wiring as wiring

    monkeypatch.setattr(wiring, "make_adapter", lambda worker, home: _Named())
    runtime = Runtime(
        config=_instances_config(executable(tmp_path / "bin" / "claude"), 2),
        home=tmp_path,
        api=None,  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="m",
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
    )
    runtime.sync_slots()
    assert [slot.observed_model for slot in runtime.slots] == ["Qwen3.8-Flash-Next IQ3_S"] * 2


def test_local_runs_are_published_into_their_claims_once(tmp_path: Path) -> None:
    # Runs from before the history was shared exist only locally; other managers need them too.
    from github_issue_worker.application.history import RunRecord

    clock = FakeClock(datetime(2026, 10, 30, tzinfo=UTC))
    store = InMemoryClaimStore()
    coordinator = Coordinator(store, clock, ClaimPolicy(timedelta(days=7), timedelta(days=1)))
    handle = coordinator.claim(3, Holder("m", "claude"), "issue/3-x")
    assert handle is not None
    coordinator.complete(handle)
    watch = RepositoryWatch(name="o/a", host=FakeHost(), coordinator=coordinator, store=store)
    runtime = Runtime(
        config=parse("{}"),
        home=tmp_path,
        api=None,  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="m",
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
        state_path=tmp_path / "state.json",
    )
    started = datetime(2026, 10, 30, 9, 0, 0, 123456, tzinfo=UTC)
    finished = started + timedelta(minutes=12)
    for issue, repository in ((3, "o/a"), (4, "o/a"), (3, "o/b")):
        runtime.history.append(
            RunRecord(
                repository,
                issue,
                "claude",
                "claude",
                "Claude Opus 5.5",
                "first",
                "merged",
                started,
                finished,
                started,
            )
        )
    runtime.publish_history(watch)
    runtime.publish_history(watch)  # hourly: nothing twice
    claim = store.current(3)
    assert claim is not None
    assert [(run.manager, run.model, run.verdict) for run in claim.runs] == [
        ("m", "Claude Opus 5.5", "merged")
    ]  # issue 4 has no claim to carry it, o/b is another repository


def test_given_a_suspended_repository_when_starting_runtime_then_no_clone_or_write_is_attempted(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from github_issue_worker.ports.github import Response
    from github_issue_worker.ports.repository import RepositorySuspended

    clock = FakeClock(datetime(2026, 10, 31, tzinfo=UTC))
    requests: list[tuple[str, str]] = []

    def request(method: str, path: str, **kwargs: object) -> Response:
        requests.append((method, path))
        assert method == "GET" and path == "repos/o/private"
        return Response(200, {}, {"default_branch": "main", "permissions": {"push": False}})

    config = parse('{"repositories": [{"repository": "o/private"}]}')
    workspace = Mock()
    workspace.clone.side_effect = AssertionError("cannot clone a repository we cannot access")
    runtime = Runtime(
        config,
        tmp_path,
        SimpleNamespace(clock=clock, request=request),  # type: ignore[arg-type]
        JsonStateStore(tmp_path / "state.json"),
        "manager",
        workspace,
    )
    manager = runtime.manager()
    manager.start()
    manager.tick()
    watch = manager.repositories[0]
    assert watch.suspension_reason == "lost push access"
    workspace.clone.assert_not_called()
    with pytest.raises(RepositorySuspended):
        watch.host.comment(1, "blocked")
    with pytest.raises(RepositorySuspended):
        watch.coordinator.claim(1, Holder("manager", "w"), "issue/1")
    assert requests == [("GET", "repos/o/private")]


class _SleepingAgent:
    """An agent that is still working when its run reaches ``runTimeout``."""

    kind = "fake"

    def invocation(self, run: AgentRun) -> Invocation:
        import sys

        return Invocation(
            argv=[sys.executable, "-c", "import time; time.sleep(30)"], cwd=run.worktree
        )

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        return RunReport(session=None, outcome=None, error=f"exit {exit_code}")


def test_a_run_past_runtimeout_is_killed_and_reported_as_stopped_by_the_time_limit(
    tmp_path: Path,
) -> None:
    report = wiring.execute(_SleepingAgent(), AgentRun(tmp_path, "work", session="s9"), 0.2)
    assert report.cut_off is not None
    assert report.cut_off.kind == "time" and report.cut_off.limit == "0.2 s"
    assert report.outcome is None and report.limit is None
    assert report.session == "s9"


def test_max_takeovers_is_wired_into_the_claim_policy_and_updated_live(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from tests.application.test_manager import Fixture

    body: dict[str, object] = {
        "repositories": [{"repository": "o/r"}],
        "fleet": {"maxTakeovers": 2},
    }
    config = parse(json.dumps(body))
    runtime = Runtime(
        config=config,
        home=tmp_path,
        api=SimpleNamespace(clock=FakeClock(datetime(2026, 10, 30, tzinfo=UTC))),  # type: ignore[arg-type]
        state=JsonStateStore(tmp_path / "state.json"),
        manager_id="m",
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
    )
    watch = runtime.watch(config.repositories[0])
    assert watch.coordinator.policy.max_takeovers == 2

    body["fleet"] = {"maxTakeovers": 5}
    f = Fixture([])
    f.manager.repositories = [watch]
    runtime.apply(parse(json.dumps(body)), f.manager)
    assert runtime.watches["o/r"].coordinator.policy.max_takeovers == 5
