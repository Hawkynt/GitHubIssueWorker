"""The UI's backend over the running manager."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from github_issue_worker.adapters.process import terminate
from github_issue_worker.application.accounts import AccountUsage, account_usage
from github_issue_worker.application.coordinator import ClaimHandle
from github_issue_worker.application.github_host import GitHubHost
from github_issue_worker.application.history import RunRecord, fleet_records, merged_history
from github_issue_worker.application.manager import Manager, WorkerSlot
from github_issue_worker.application.repository_suggestions import (
    PushableRepositories,
    pushable_repositories,
)
from github_issue_worker.configuration import ClaudeLocalInstallation, Configuration, save
from github_issue_worker.domain.claim import ClaimChange, claim_history
from github_issue_worker.domain.issue import Issue
from github_issue_worker.domain.model_name import display_model
from github_issue_worker.domain.outcome import Outcome, OutcomeError
from github_issue_worker.domain.work import SKIP_LABEL
from github_issue_worker.ports.agents import JOURNAL_FILE, OUTCOME_FILE
from github_issue_worker.ports.claims import ClaimStoreError
from github_issue_worker.ports.github import GhUnavailable, GitHubError
from github_issue_worker.ui.model import (
    ActionError,
    ClaimView,
    IssueDetail,
    IssueView,
    RepositoryView,
    Snapshot,
    WorkerState,
    WorkerView,
    tail,
)
from github_issue_worker.wiring import Runtime

log = logging.getLogger(__name__)

AGENT_NAMES = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
    "opencode": "OpenCode",
    "command": "Command",
    "remote": "Remote agent",
}
CLAIM_REFRESH = 30.0
"""Seconds between background refreshes of the claims: reading them costs a git fetch each."""
HISTORY_VERSIONS = 500
"""Claim versions read for the Issue page's claim history, newest first."""
HISTORY_ROWS = 50
"""Rows of claim history the Issue page shows, newest first."""


_LABEL_STATES = (
    ("worker:skip", "skipped (worker:skip)"),
    ("worker:needs-input", "waiting for your answer"),
    ("worker:awaiting-review", "waiting for review or CI"),
    ("worker:failed", "failed; its last comment says why"),
    ("worker:unserved", "no worker can serve it"),
    ("worker:split", "split; waiting for its sub-issues"),
    ("worker:queued", "waiting for a worker"),
)


def issue_state(labels: frozenset[str], claim_state: str | None, local: bool) -> str:
    """What is happening with an open issue, in words: from its claim first, else its labels."""
    if claim_state == "paused":
        return "paused until the usage limit resets"
    if claim_state == "working":
        return "worked here" if local else "worked elsewhere"
    for label, words in _LABEL_STATES:
        if label in labels:
            return words
    return "not picked up"


def worker_model(slot: WorkerSlot, history: list[RunRecord]) -> str:
    """What this run reported, else what is configured, else the model of its last finished run."""
    known = slot.observed_model or slot.model
    if known:
        return display_model(known)
    return next((r.model for r in reversed(history) if r.worker == slot.name), "-")


def _read(path: Path) -> str | None:
    """A file of the worktree as text, or None when it is not there."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _outcome(path: Path) -> Outcome | None:
    """The worktree's outcome, or None when there is none or it breaks the contract."""
    text = _read(path)
    if text is None:
        return None
    try:
        return Outcome.parse(text)
    except OutcomeError:
        return None


def open_folder(path: Path) -> None:
    """Hand a folder to the platform's file manager, without waiting for it."""
    opener = {"win32": "explorer", "darwin": "open"}.get(sys.platform, "xdg-open")
    subprocess.Popen(
        [opener, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class RuntimeBackend:
    def __init__(
        self,
        runtime: Runtime,
        manager: Manager,
        config_path: Path,
        detect: Callable[[Path], list[dict[str, object]]],
    ) -> None:
        self._runtime = runtime
        self._manager = manager
        self._path = config_path
        self._detect = detect
        self._claims: tuple[ClaimView, ...] = ()
        self._checkpoints: dict[tuple[str, int], ClaimView] = {}
        self._open: dict[str, list[Issue]] = {}
        self._history: list[RunRecord] = []
        self._fleet: list[RunRecord] = []
        self._history_at = 0.0
        self._claims_at = 0.0
        self._refreshing = threading.Lock()
        self._issue_cache: dict[tuple[str, int], tuple[float, tuple[ClaimChange, ...]]] = {}
        self._pushable = PushableRepositories(lambda: pushable_repositories(self._runtime.api))
        self._pushable.refresh()

    # --- reading ---------------------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        now = self._runtime.api.clock.now()
        self._refresh_claims()
        workers = tuple(self._worker(slot, now) for slot in self._manager.workers)
        counts: dict[str, dict[str, int]] = {}
        for claim in self._claims:
            bucket = counts.setdefault(claim.repository, {})
            bucket[claim.state] = bucket.get(claim.state, 0) + 1
        repositories = tuple(
            RepositoryView(
                settings.repository,
                self._runtime.state.poll_state(settings.repository).watched_since,
                counts.get(settings.repository, {}),
            )
            for settings in self._runtime.config.repositories
        )
        return Snapshot(
            self._runtime.manager_id,
            now,
            workers,
            repositories,
            self._claims,
            self._issues(now),
            self._accounts(now),
        )

    def _accounts(self, now: datetime) -> tuple[AccountUsage, ...]:
        paused = (
            (claim.repository, claim.issue, claim.holder.rpartition("/")[2])
            for claim in self._claims
            if claim.mine and claim.state == "paused"
        )
        return account_usage(
            self._runtime.config.accounts,
            {slot.name: slot.account for slot in self._manager.workers},
            self._runtime.state.limited_until,
            paused,
            now,
        )

    def _issues(self, now: datetime) -> tuple[IssueView, ...]:
        claims = {(claim.repository, claim.issue): claim for claim in self._claims}
        slots = {slot.current: slot for slot in self._manager.workers if slot.current}
        found = []
        for repository, issues in self._open.items():
            for issue in issues:
                claim = claims.get((repository, issue.number))
                checkpoint = self._checkpoints.get((repository, issue.number))
                slot = slots.get((repository, issue.number))
                local = slot is not None or (claim is not None and claim.mine)
                manager = worker = None
                if claim is not None:
                    manager, _, worker = claim.holder.rpartition("/")
                if slot is not None:
                    worker, manager = slot.name, self._runtime.manager_id
                known = (slot.observed_model or slot.model) if slot else None
                found.append(
                    IssueView(
                        repository=repository,
                        number=issue.number,
                        title=issue.title,
                        state=f"blocked: {slot.blocker.reason}"
                        if slot and slot.blocker
                        else issue_state(
                            issue.labels,
                            "working" if slot else claim.state if claim else None,
                            local,
                        ),
                        worker=worker,
                        manager=manager,
                        local=local,
                        model=display_model(known)
                        if known
                        else (claim.model if claim and claim.model else None),
                        step=(slot.step if slot else claim.step if claim else None),
                        since=slot.started_at if slot else claim.renewed if claim else None,
                        merge_conflict=(
                            slot.merge_conflict
                            if slot and slot.busy
                            else checkpoint.merge_conflict
                            if checkpoint
                            else False
                        ),
                        wait_reason=claim.wait_reason if claim and not slot else "",
                        paused_until=claim.paused_until if claim and not slot else None,
                        body=issue.body,
                    )
                )
        return tuple(found)

    def _worker(self, slot: WorkerSlot, now) -> WorkerView:  # type: ignore[no-untyped-def]
        settings = next((w for w in self._runtime.config.workers if w.name == slot.group), None)
        local = settings is not None and isinstance(settings.installation, ClaudeLocalInstallation)
        agent = "ClaudeLocal" if local else AGENT_NAMES.get(slot.agent, slot.agent)
        model = worker_model(slot, self.history())
        limited = self._runtime.state.limited_until(slot.account)
        claim = next(
            (
                c
                for c in self._claims
                if c.mine and c.holder == f"{self._runtime.manager_id}/{slot.name}"
            ),
            None,
        )
        state: WorkerState
        if not slot.available:
            state = "unavailable"
        elif slot.busy and slot.current:
            state = "working"
        elif not slot.enabled:
            state = "paused"
        elif (limited and limited > now) or (claim and claim.state == "paused"):
            state = "limited"
        else:
            state = "idle"
        repository, issue = (
            slot.current
            if slot.current
            else (claim.repository, claim.issue)
            if claim
            else (None, None)
        )
        worktree = (
            str(self._runtime.workspace.worktree_path(repository, issue))
            if repository and issue
            else None
        )
        return WorkerView(
            name=slot.name,
            group=slot.group,
            blocker=slot.blocker.reason if slot.blocker else "",
            blocker_url=slot.blocker.url if slot.blocker else "",
            agent=agent,
            account=slot.account,
            model=model,
            state=state,
            reason=claim.wait_reason if claim and not slot.busy else slot.reason,
            limited_until=claim.paused_until if claim and claim.paused_until else limited,
            repository=repository,
            issue=issue,
            issue_title=slot.issue_title,
            step=slot.step if slot.busy else claim.step if claim else None,
            branch=slot.branch or (claim.branch if claim else None),
            worktree=worktree,
            since=slot.started_at,
            merge_conflict=slot.merge_conflict
            if slot.busy
            else claim.merge_conflict
            if claim
            else False,
            agent_kind=slot.agent,
        )

    def _refresh_claims(self) -> None:
        if time.monotonic() - self._claims_at < CLAIM_REFRESH or not self._refreshing.acquire(
            False
        ):
            return

        def refresh() -> None:
            try:
                found: list[ClaimView] = []
                checkpoints: dict[tuple[str, int], ClaimView] = {}
                fleet: list[RunRecord] = []
                for name, watch in list(self._runtime.watches.items()):
                    claims = []
                    for number in watch.store.issues():
                        stored = watch.store.read(number)
                        if stored is None:
                            continue
                        claim = stored.claim
                        claims.append(claim)
                        view = ClaimView(
                            repository=name,
                            issue=number,
                            holder=str(claim.holder),
                            mine=claim.holder.manager == self._runtime.manager_id,
                            state=claim.state.value,
                            step=claim.step,
                            renewed=claim.renewed_at,
                            expires=claim.expires_at,
                            branch=claim.branch,
                            paused_until=claim.paused_until,
                            merge_conflict=claim.merge_conflict,
                            wait_reason=claim.wait_reason,
                            model=claim.model,
                        )
                        checkpoints[(name, number)] = view
                        if claim.state.holds:
                            found.append(view)
                    fleet += fleet_records(name, claims)
                self._fleet = fleet
                self._checkpoints = checkpoints
                self._claims = tuple(found)
                self._open = {
                    name: watch.host.open_issues()
                    for name, watch in list(self._runtime.watches.items())
                }
            except Exception:
                log.exception("could not read the claims")
            finally:
                self._claims_at = time.monotonic()
                self._refreshing.release()

        threading.Thread(target=refresh, daemon=True).start()

    def config(self) -> Configuration:
        return self._runtime.config

    def pushable_repositories(self) -> tuple[str, ...]:
        return self._pushable.names()

    # --- changing --------------------------------------------------------------------------------

    def save_config(self, config: Configuration) -> None:
        self._runtime.apply(config, self._manager)
        save(config, self._path)

    def check_repository(self, repository: str) -> None:
        try:
            info = GitHubHost(self._runtime.api, repository).info()
        except GitHubError as error:
            raise ActionError(f"{repository}: {error}") from None
        except GhUnavailable as error:
            raise ActionError(f"gh cannot reach GitHub ({error}); run `gh auth login`") from None
        if not info.can_push:
            raise ActionError(
                f"{repository}: your account cannot push there, so it cannot be watched"
            )

    def detect_workers(self) -> list[dict[str, object]]:
        return self._detect(self._runtime.home)

    def set_worker_enabled(self, name: str, enabled: bool) -> None:
        for slot in self._manager.workers:
            if name in (slot.name, slot.group):
                slot.enabled = enabled

    def approve_ci(self, name: str) -> None:
        slot = next((s for s in self._manager.workers if s.name == name), None)
        if slot is None or slot.blocker is None or not slot.blocker.approvals or not slot.current:
            raise ActionError(f"{name} has no CI run waiting for an approval")
        watch = self._runtime.watches.get(slot.current[0])
        if watch is None:
            raise ActionError(f"{slot.current[0]} is no longer watched")
        for run in slot.blocker.approvals:
            watch.host.approve_run(run)
        slot.blocker = None

    def stop_worker(self, name: str) -> None:
        slot = next((s for s in self._manager.workers if s.name == name), None)
        if slot is None or not slot.busy:
            raise ActionError(f"{name} is not working on an issue, so there is nothing to stop")
        slot.enabled = False
        if not terminate(name):
            raise ActionError(
                f"{name} is paused, but it is not running its agent right now (it is verifying or "
                "waiting for checks), so it finishes this issue and then takes nothing new"
            )
        self._claims_at = 0.0

    def open_worktree(self, name: str) -> None:
        worker = next((w for w in self.snapshot().workers if w.name == name), None)
        if worker is None or worker.worktree is None:
            raise ActionError(f"{name} is not working on an issue, so it has no worktree to open")
        path = Path(worker.worktree)
        if not path.is_dir():
            raise ActionError(f"the worktree {path} does not exist (yet)")
        open_folder(path)

    def history(self) -> list[RunRecord]:
        """Every run of the fleet the claims carry, plus older local ones (F14.13)."""
        if time.monotonic() - self._history_at > CLAIM_REFRESH:
            self._history = self._runtime.history.load()
            self._history_at = time.monotonic()
        return merged_history(self._history, self._fleet)

    def log(self, name: str) -> str | None:
        slot = next((s for s in self._manager.workers if s.name == name), None)
        if slot is None or slot.log_path is None or not slot.log_path.exists():
            return None
        return slot.log_path.read_text(encoding="utf-8", errors="replace")

    # --- one issue (F14.6) ------------------------------------------------------------------------

    def issue(self, repository: str, number: int) -> IssueDetail | None:
        watch = self._runtime.watches.get(repository)
        if watch is None:
            return None
        self._refresh_claims()
        key = (repository, number)
        claim = self._checkpoints.get(key)
        now = self._runtime.api.clock.now()
        view = next(
            (found for found in self._issues(now) if (found.repository, found.number) == key),
            None,
        )
        if view is None:
            if claim is None:
                return None
            view = IssueView(repository, number, "", f"not open; its claim is {claim.state}")
        slot = next(
            (s for s in self._manager.workers if s.busy and s.current == key),
            None,
        )
        log = self.log(slot.name) if slot is not None else None
        worktree = self._runtime.workspace.worktree_path(repository, number)
        return IssueDetail(
            issue=view,
            claim=claim,
            runs=tuple(
                sorted(
                    (r for r in self.history() if (r.repository, r.issue) == key),
                    key=lambda run: run.started,
                )
            ),
            history=self._claim_history(repository, number),
            journal=_read(worktree / JOURNAL_FILE),
            outcome=_outcome(worktree / OUTCOME_FILE),
            log=tail(log) if log is not None else None,
            log_worker=slot.name if slot is not None else None,
        )

    def _claim_history(self, repository: str, number: int) -> tuple[ClaimChange, ...]:
        """The folded claim history, read at most every `CLAIM_REFRESH` seconds per issue."""
        key = (repository, number)
        cached = self._issue_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < CLAIM_REFRESH:
            return cached[1]
        try:
            versions = self._runtime.watches[repository].store.history(number, HISTORY_VERSIONS)
        except ClaimStoreError:
            log.exception("could not read the claim history of %s#%d", repository, number)
            return cached[1] if cached else ()
        complete = len(versions) < HISTORY_VERSIONS
        rows = tuple(claim_history(versions, complete)[:HISTORY_ROWS])
        self._issue_cache[key] = (time.monotonic(), rows)
        return rows

    def pull_request(self, repository: str, number: int) -> str | None:
        watch = self._runtime.watches.get(repository)
        claim = self._checkpoints.get((repository, number))
        if watch is None or claim is None or not claim.branch:
            return None
        try:
            pull = watch.host.find_pull_request(claim.branch)
        except (GitHubError, GhUnavailable):
            log.exception("could not look up the pull request of %s#%d", repository, number)
            return None
        return pull.url if pull is not None and pull.url else None

    def skip(self, repository: str, number: int) -> None:
        watch = self._runtime.watches.get(repository)
        if watch is None:
            raise ActionError(f"{repository} is not watched")
        try:
            watch.host.add_labels(number, [SKIP_LABEL])
        except GitHubError as error:
            raise ActionError(f"{repository}#{number}: {error}") from None
        except GhUnavailable as error:
            raise ActionError(f"gh cannot reach GitHub ({error}); run `gh auth login`") from None

    def release(self, repository: str, issue: int, force: bool) -> None:
        watch = self._runtime.watches.get(repository)
        stored = watch.store.read(issue) if watch else None
        if watch is None or stored is None or not stored.claim.state.holds:
            raise ActionError(f"{repository}#{issue} holds no claim")
        if stored.claim.holder.manager != self._runtime.manager_id and not force:
            raise ActionError(f"{repository}#{issue} is claimed by {stored.claim.holder}")
        watch.coordinator.release(ClaimHandle(stored.version, stored.claim), step="released")
        self._claims_at = 0.0
