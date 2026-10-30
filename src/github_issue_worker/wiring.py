"""Puts the real adapters together into a running manager."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from github_issue_worker.adapters.agents.antigravity import AntigravityAdapter
from github_issue_worker.adapters.agents.claude import ClaudeAdapter, ClaudeLocalAdapter
from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.adapters.gh_api import GhApi
from github_issue_worker.adapters.git_claims import GitClaimStore
from github_issue_worker.adapters.home import account_home
from github_issue_worker.adapters.installations import Unavailable, resolve
from github_issue_worker.adapters.process import run_invocation
from github_issue_worker.adapters.state_store import JsonStateStore
from github_issue_worker.adapters.verifier import Verifier
from github_issue_worker.adapters.workspace import Workspace
from github_issue_worker.application.coordinator import ClaimHandle, Coordinator
from github_issue_worker.application.github_host import GitHubHost
from github_issue_worker.application.issue_run import IssueRun, RunSettings, RunStopped, Verdict
from github_issue_worker.application.manager import Manager, RepositoryWatch, WorkerSlot
from github_issue_worker.application.poller import Poller, PollState
from github_issue_worker.configuration import Configuration, RepositorySettings, WorkerSettings
from github_issue_worker.domain.claim import ClaimPolicy
from github_issue_worker.domain.commit_message import DEFAULT_FORBIDDEN_WORDS, CommitGate
from github_issue_worker.domain.issue import Comment, Issue
from github_issue_worker.domain.model_name import display_model
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentAdapter, AgentRun, Invocation, RunReport

log = logging.getLogger(__name__)


def state_dir(home: Path) -> Path:
    return home / ".local" / "state" / "GitHubIssueWorker"


def log_dir(state: Path, repository: str, issue: int) -> Path:
    owner, name = repository.split("/", 1)
    return state / "logs" / owner / name / str(issue)


def cache_dir(home: Path) -> Path:
    return home / ".cache" / "GitHubIssueWorker"


def remote_url(repository: str) -> str:
    return f"git@github.com:{repository}.git"


@dataclass(frozen=True, slots=True)
class RepositoryRun:
    """The merged defaults and overrides one repository's runs use."""

    merge_method: str
    base_branch: str | None
    ci_repair_attempts: int
    run_timeout: float
    checks_timeout: float
    build: str | None
    test: str | None
    forbidden_words: tuple[str, ...] = DEFAULT_FORBIDDEN_WORDS

    @property
    def gate(self) -> CommitGate:
        return CommitGate(forbidden_words=self.forbidden_words)


def repository_run(config: Configuration, repository: RepositorySettings) -> RepositoryRun:
    defaults = config.defaults

    def pick(name: str) -> object:
        value = getattr(repository, name)
        return getattr(defaults, name) if value is None else value

    return RepositoryRun(
        merge_method=str(pick("merge_method")),
        base_branch=pick("base_branch"),  # type: ignore[arg-type]
        ci_repair_attempts=int(pick("ci_repair_attempts")),  # type: ignore[call-overload]
        run_timeout=pick("run_timeout").total_seconds(),  # type: ignore[attr-defined]
        checks_timeout=pick("checks_timeout").total_seconds(),  # type: ignore[attr-defined]
        build=pick("build"),  # type: ignore[arg-type]
        test=pick("test"),  # type: ignore[arg-type]
        forbidden_words=tuple(pick("forbidden_words")),  # type: ignore[arg-type]
    )


def make_adapter(worker: WorkerSettings, home: Path) -> AgentAdapter:
    resolved = resolve(worker, home)
    executable = str(resolved.executable)
    if resolved.local:
        return ClaudeLocalAdapter(resolved.executable, env=resolved.env)
    if worker.agent == "claude":
        return ClaudeAdapter(executable, env=resolved.env, budget_usd=worker.max_budget_usd_per_run)
    if worker.agent == "codex":
        return CodexAdapter(executable, env=resolved.env)
    if worker.agent == "antigravity":
        return AntigravityAdapter(executable, env=resolved.env)
    raise Unavailable(f"{worker.name}: the {worker.agent} adapter is not implemented yet")


def execute(
    adapter: AgentAdapter,
    run: AgentRun,
    timeout: float,
    log: Path | None = None,
    name: str | None = None,
) -> RunReport:
    """One agent run. Its output streams to ``log``; ``name`` lets the UI stop it (F14.4)."""
    invocation = adapter.invocation(run)
    result = run_invocation(
        Invocation(invocation.argv, invocation.cwd, invocation.stdin, dict(invocation.env)),
        timeout,
        log=log,
        name=name,
    )
    if result.killed:
        raise RunStopped("stopped by the maintainer")
    if result.timed_out:
        return RunReport(
            session=run.session, outcome=None, error=f"timed out after {timeout:.0f} s"
        )
    return adapter.report(run, result.exit_code, result.stdout, result.stderr)


class _PollStates(MutableMapping[str, PollState]):
    def __init__(self, state: JsonStateStore) -> None:
        self._state = state

    def __getitem__(self, key: str) -> PollState:
        return self._state.poll_state(key)

    def __setitem__(self, key: str, value: PollState) -> None:
        self._state.set_poll_state(key, value)

    def __delitem__(self, key: str) -> None:
        self._state.set_poll_state(key, PollState())

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


@dataclass
class Runtime:
    config: Configuration
    home: Path
    api: GhApi
    state: JsonStateStore
    manager_id: str
    workspace: Workspace
    watches: dict[str, RepositoryWatch] = field(default_factory=dict)
    state_path: Path = Path("state.json")
    adapters: dict[str, AgentAdapter] = field(default_factory=dict)
    slots: list[WorkerSlot] = field(default_factory=list)

    def watch(self, settings: RepositorySettings) -> RepositoryWatch:
        name = settings.repository
        if name not in self.watches:
            clone = self.workspace.clone(name)
            store = GitClaimStore(clone, home=self.home, push_url=remote_url(name))
            fleet = self.config.fleet
            coordinator = Coordinator(
                store, self.api.clock, ClaimPolicy(fleet.claim_validity, fleet.claim_grace)
            )
            host = GitHubHost(self.api, name)
            self.watches[name] = RepositoryWatch(
                name=name,
                host=host,
                coordinator=coordinator,
                store=store,
                mode=settings.mode or self.config.defaults.mode,
                process_backlog=bool(
                    settings.process_backlog
                    if settings.process_backlog is not None
                    else self.config.defaults.process_backlog
                ),
                settings=repository_run(self.config, settings),
            )
        return self.watches[name]

    def run_issue(
        self,
        watch: RepositoryWatch,
        slot: WorkerSlot,
        handle: ClaimHandle,
        issue: Issue,
        comments: tuple[Comment, ...],
        kind: RunKind,
    ) -> Verdict:
        run = watch.settings
        assert isinstance(run, RepositoryRun)
        adapter = self.adapters[slot.name]
        info = watch.host.info()
        method = (
            run.merge_method if run.merge_method in info.merge_methods else info.merge_methods[0]
        )
        return IssueRun(
            host=watch.host,
            workspace=self.workspace,
            coordinator=watch.coordinator,
            agent=lambda agent_run: self._observe(
                slot,
                execute(
                    adapter,
                    agent_run,
                    run.run_timeout,
                    log=self._new_log(slot, watch.name, issue.number),
                    name=slot.name,
                ),
            ),
            verify=Verifier(build=run.build, test=run.test, timeout=run.run_timeout).verify,
            sessions=self.state.sessions(watch.name),
            clock=self.api.clock,
            sleep=time.sleep,
            settings=RunSettings(
                worker=slot.name,
                model=slot.model,
                merge_method=method,
                base_branch=run.base_branch,
                ci_repair_attempts=run.ci_repair_attempts,
                checks_timeout=timedelta(seconds=run.checks_timeout),
                gate=run.gate,
            ),
            progress=lambda step: setattr(slot, "step", step),
        ).run(handle, issue, comments, kind)

    def _new_log(self, slot: WorkerSlot, repository: str, issue: int) -> Path:
        """Each agent run's output gets its own file, kept for the issue's log page."""
        stamp = self.api.clock.now().strftime("%Y%m%dT%H%M%S")
        path = log_dir(self.state_path.parent, repository, issue) / f"{stamp}-{slot.name}.ndjson"
        slot.log_path = path
        return path

    def _observe(self, slot: WorkerSlot, report: RunReport) -> RunReport:
        """Remember which model the run reported, for status, the log and the UI."""
        if report.model and report.model != slot.observed_model:
            slot.observed_model = report.model
            self.state.set_model(slot.name, report.model)
            log.info("worker %s runs on %s", slot.name, display_model(report.model))
        return report

    def apply(self, config: Configuration, manager: Manager) -> None:
        """Apply a changed configuration without a restart (F0.7).

        Repositories and workers are added and removed, existing ones updated. A worker that is
        busy when it is removed finishes its current run first and then takes nothing new.
        """
        self.config = config
        manager.trusted_users = tuple(config.trusted_users)
        manager.manager_tags = frozenset(config.manager.tags)

        wanted = {settings.repository: settings for settings in config.repositories}
        for name in list(self.watches):
            if name not in wanted:
                watch = self.watches.pop(name)
                if watch in manager.repositories:
                    manager.repositories.remove(watch)
        for name, settings in wanted.items():
            if name in self.watches:
                watch = self.watches[name]
                watch.settings = repository_run(config, settings)
                watch.mode = settings.mode or config.defaults.mode
            else:
                watch = self.watch(settings)
                manager.repositories.append(watch)
                watch.host.ensure_worker_labels()

        configured = {worker.name: worker for worker in config.workers}
        for slot in list(self.slots):
            if slot.name not in configured:
                if slot.busy:
                    slot.enabled = False
                    slot.reason = "removed; finishing its current run"
                else:
                    self.slots.remove(slot)
                    self.adapters.pop(slot.name, None)
        for worker in config.workers:
            existing = next((s for s in self.slots if s.name == worker.name), None)
            if existing is None:
                existing = WorkerSlot(name=worker.name, agent=worker.agent, account=worker.account)
                self.slots.append(existing)
            slot = existing
            slot.agent, slot.account, slot.priority = worker.agent, worker.account, worker.priority
            slot.tags, slot.model = frozenset(worker.tags), worker.model
            try:
                self.adapters[worker.name] = make_adapter(worker, self.home)
                slot.available, slot.reason = True, ""
            except Unavailable as unavailable:
                slot.available, slot.reason = False, str(unavailable)
        manager.workers = self.slots

    def manager(self) -> Manager:
        for settings in self.config.repositories:
            self.watch(settings)
        return Manager(
            manager_id=self.manager_id,
            manager_tags=frozenset(self.config.manager.tags),
            repositories=list(self.watches.values()),
            workers=self.slots,
            poll=Poller(self.api, self.api.clock).poll,
            poll_states=_PollStates(self.state),
            limits=self.state,
            clock=self.api.clock,
            trusted_users=tuple(self.config.trusted_users),
            run_issue=self.run_issue,
            spawn=lambda job: threading.Thread(target=job, daemon=True).start(),
        )


def build_runtime(
    config: Configuration,
    home: Path | None = None,
    state_path: Path | None = None,
    cache: Path | None = None,
) -> Runtime:
    home = home or account_home(config.manager.home)
    state_path = state_path or state_dir(home) / "state.json"
    state = JsonStateStore(state_path)
    runtime = Runtime(
        config=config,
        home=home,
        api=GhApi(home=home),
        state=state,
        manager_id=state.manager_id(config.manager.name or socket.gethostname()),
        workspace=Workspace(cache or cache_dir(home), remote_url, home),
        state_path=state_path,
    )
    for worker in config.workers:
        slot = WorkerSlot(
            name=worker.name,
            agent=worker.agent,
            account=worker.account,
            priority=worker.priority,
            tags=frozenset(worker.tags),
            model=worker.model,
            observed_model=state.model(worker.name),
        )
        try:
            runtime.adapters[worker.name] = make_adapter(worker, home)
        except Unavailable as unavailable:
            slot.available = False
            slot.reason = str(unavailable)
            log.warning("worker %s is unavailable: %s", worker.name, unavailable)
        runtime.slots.append(slot)
    return runtime
