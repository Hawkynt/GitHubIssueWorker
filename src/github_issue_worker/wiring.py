"""Puts the real adapters together into a running manager."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field, replace
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
from github_issue_worker.application.history import HistoryStore, RunRecord
from github_issue_worker.application.housekeeping import Housekeeping
from github_issue_worker.application.issue_run import IssueRun, RunSettings, RunStopped, Verdict
from github_issue_worker.application.manager import (
    Manager,
    RepositoryWatch,
    WorkerSlot,
    instance_name,
)
from github_issue_worker.application.poller import Poller, PollState
from github_issue_worker.configuration import Configuration, RepositorySettings, WorkerSettings
from github_issue_worker.domain.claim import ClaimPolicy, RunEntry
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
    max_prose_lines: int = 2
    split_issues: bool = True

    @property
    def gate(self) -> CommitGate:
        return CommitGate(
            forbidden_words=self.forbidden_words, max_prose_lines=self.max_prose_lines
        )


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
        max_prose_lines=int(pick("max_prose_lines")),  # type: ignore[call-overload]
        split_issues=bool(pick("split_issues")),
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
            store = GitClaimStore(
                lambda: self.workspace.clone(name), home=self.home, push_url=remote_url(name)
            )
            fleet = self.config.fleet
            coordinator = Coordinator(
                store, self.api.clock, ClaimPolicy(fleet.claim_validity, fleet.claim_grace)
            )
            host = GitHubHost(self.api, name)
            watch = RepositoryWatch(
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
            watch.coordinator = replace(coordinator, before_write=watch.require_access)
            host.before_write = watch.require_access
            self.watches[name] = watch
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
        watch.require_access()
        run = watch.settings
        assert isinstance(run, RepositoryRun)
        adapter = self.adapters[slot.group]
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
                split_issues=run.split_issues,
            ),
            progress=lambda step: setattr(slot, "step", step),
            conflict_changed=lambda conflict: setattr(slot, "merge_conflict", conflict),
            blocked=lambda blocker: setattr(slot, "blocker", blocker),
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
            self.state.set_model(slot.group, report.model)
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
                manager.prepare(watch)

        self.sync_slots()
        manager.workers = self.slots
        manager.max_runs = self.max_runs

    def max_runs(self, account: str) -> int:
        settings = self.config.accounts.get(account)
        return settings.max_concurrent_runs if settings else 1

    def sync_slots(self) -> None:
        """One slot per instance: as many per worker as its account runs at once (F12.11).

        Instances no longer wanted are dropped, or, while busy, finish their run and take
        nothing new.
        """
        wanted = {
            instance_name(worker.name, number): worker
            for worker in self.config.workers
            for number in range(1, self.max_runs(worker.account) + 1)
        }
        for slot in list(self.slots):
            if slot.name not in wanted:
                if slot.busy:
                    slot.enabled = False
                    slot.reason = "removed; finishing its current run"
                else:
                    self.slots.remove(slot)
        for worker in self.config.workers:
            try:
                self.adapters[worker.name] = make_adapter(worker, self.home)
                available, reason = True, ""
                if worker.model is None and self.state.model(worker.name) is None:
                    default = getattr(self.adapters[worker.name], "default_model", None)
                    known = default() if callable(default) else None
                    if known:
                        self.state.set_model(worker.name, known)
            except Unavailable as unavailable:
                self.adapters.pop(worker.name, None)
                available, reason = False, str(unavailable)
                log.warning("worker %s is unavailable: %s", worker.name, unavailable)
            for name in (n for n, w in wanted.items() if w is worker):
                instance = next((s for s in self.slots if s.name == name), None)
                if instance is None:
                    instance = WorkerSlot(
                        name=name,
                        agent=worker.agent,
                        account=worker.account,
                        worker=worker.name,
                        observed_model=self.state.model(worker.name),
                    )
                    self.slots.append(instance)
                instance.agent, instance.account, instance.priority = (
                    worker.agent,
                    worker.account,
                    worker.priority,
                )
                instance.tags, instance.model, instance.worker = (
                    frozenset(worker.tags),
                    worker.model,
                    worker.name,
                )
                instance.available, instance.reason = available, reason
        configured = {worker.name for worker in self.config.workers}
        for name in list(self.adapters):
            if name not in configured and not any(s.group == name for s in self.slots):
                self.adapters.pop(name)

    def _sweep(self, watch: RepositoryWatch, mine: set[int], claimed: set[int]) -> None:
        Housekeeping(watch.host, self.workspace).sweep(mine, claimed)
        self.publish_history(watch)

    def publish_history(self, watch: RepositoryWatch) -> None:
        """Put this manager's runs the claims do not carry yet into them (F14.13).

        Runs that finished before the history was shared exist only in the local file; once in
        the claims, a manager on any machine counts them too.
        """
        known = set(watch.store.issues())
        for record in self.history.load():
            if record.repository != watch.name or record.issue not in known:
                continue
            if record.manager not in ("", self.manager_id):
                continue
            watch.coordinator.record_run(
                record.issue,
                RunEntry(
                    self.manager_id,
                    record.worker,
                    record.agent,
                    record.model,
                    record.kind,
                    record.verdict,
                    record.started,
                    record.finished,
                ),
            )

    @property
    def history(self) -> HistoryStore:
        return HistoryStore(self.state_path.parent / "history.jsonl")

    def record(self, run: RunRecord) -> None:
        """Keep a finished run, dated from the issue's first run since its last merge."""
        history = self.history
        started = history.issue_started(run.repository, run.issue, run.started)
        history.append(replace(run, issue_started=started))

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
            on_finished=self.record,
            max_runs=self.max_runs,
            sweep=self._sweep,
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
    runtime.sync_slots()
    return runtime
