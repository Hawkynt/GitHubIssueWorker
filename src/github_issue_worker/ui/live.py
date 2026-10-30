"""The UI's backend over the running manager."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from github_issue_worker.adapters.process import terminate
from github_issue_worker.application.coordinator import ClaimHandle
from github_issue_worker.application.github_host import GitHubHost
from github_issue_worker.application.manager import Manager, WorkerSlot
from github_issue_worker.configuration import ClaudeLocalInstallation, Configuration, save
from github_issue_worker.domain.issue import Issue
from github_issue_worker.domain.model_name import display_model
from github_issue_worker.ports.github import GhUnavailable, GitHubError
from github_issue_worker.ui.model import (
    ActionError,
    ClaimView,
    IssueView,
    RepositoryView,
    Snapshot,
    WorkerState,
    WorkerView,
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


_LABEL_STATES = (
    ("worker:skip", "skipped (worker:skip)"),
    ("worker:needs-input", "waiting for your answer"),
    ("worker:awaiting-review", "waiting for review or CI"),
    ("worker:failed", "failed; its last comment says why"),
    ("worker:unserved", "no worker can serve it"),
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
        self._open: dict[str, list[Issue]] = {}
        self._claims_at = 0.0
        self._refreshing = threading.Lock()

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
            self._runtime.manager_id, now, workers, repositories, self._claims, self._issues(now)
        )

    def _issues(self, now: datetime) -> tuple[IssueView, ...]:
        claims = {(claim.repository, claim.issue): claim for claim in self._claims}
        slots = {slot.current: slot for slot in self._manager.workers if slot.current}
        found = []
        for repository, issues in self._open.items():
            for issue in issues:
                claim = claims.get((repository, issue.number))
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
                        state=issue_state(
                            issue.labels,
                            "working" if slot else claim.state if claim else None,
                            local,
                        ),
                        worker=worker,
                        manager=manager,
                        local=local,
                        model=display_model(known) if known else None,
                        step=(slot.step if slot else claim.step if claim else None),
                        since=slot.started_at if slot else claim.renewed if claim else None,
                    )
                )
        return tuple(found)

    def _worker(self, slot: WorkerSlot, now) -> WorkerView:  # type: ignore[no-untyped-def]
        settings = next((w for w in self._runtime.config.workers if w.name == slot.name), None)
        local = settings is not None and isinstance(settings.installation, ClaudeLocalInstallation)
        agent = "ClaudeLocal" if local else AGENT_NAMES.get(slot.agent, slot.agent)
        known = slot.observed_model or slot.model
        model = display_model(known) if known else "-"
        limited = self._runtime.state.limited_until(slot.account)
        state: WorkerState
        if not slot.available:
            state = "unavailable"
        elif slot.busy and slot.current:
            state = "working"
        elif not slot.enabled:
            state = "paused"
        elif limited and limited > now:
            state = "limited"
        else:
            state = "idle"
        repository, issue = slot.current if slot.current else (None, None)
        worktree = (
            str(self._runtime.workspace.worktree_path(repository, issue))
            if repository and issue
            else None
        )
        return WorkerView(
            name=slot.name,
            agent=agent,
            account=slot.account,
            model=model,
            state=state,
            reason=slot.reason,
            limited_until=limited,
            repository=repository,
            issue=issue,
            issue_title=slot.issue_title,
            step=slot.step,
            branch=slot.branch,
            worktree=worktree,
            since=slot.started_at,
        )

    def _refresh_claims(self) -> None:
        if time.monotonic() - self._claims_at < CLAIM_REFRESH or not self._refreshing.acquire(
            False
        ):
            return

        def refresh() -> None:
            try:
                found: list[ClaimView] = []
                for name, watch in list(self._runtime.watches.items()):
                    for number in watch.store.issues():
                        stored = watch.store.read(number)
                        if stored is None or not stored.claim.state.holds:
                            continue
                        claim = stored.claim
                        found.append(
                            ClaimView(
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
                            )
                        )
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

    # --- changing --------------------------------------------------------------------------------

    def save_config(self, config: Configuration) -> None:
        save(config, self._path)
        self._runtime.apply(config, self._manager)

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
            if slot.name == name:
                slot.enabled = enabled

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

    def log(self, name: str) -> str | None:
        slot = next((s for s in self._manager.workers if s.name == name), None)
        if slot is None or slot.log_path is None or not slot.log_path.exists():
            return None
        return slot.log_path.read_text(encoding="utf-8", errors="replace")

    def release(self, repository: str, issue: int, force: bool) -> None:
        watch = self._runtime.watches.get(repository)
        stored = watch.store.read(issue) if watch else None
        if watch is None or stored is None or not stored.claim.state.holds:
            raise ActionError(f"{repository}#{issue} holds no claim")
        if stored.claim.holder.manager != self._runtime.manager_id and not force:
            raise ActionError(f"{repository}#{issue} is claimed by {stored.claim.holder}")
        watch.coordinator.release(ClaimHandle(stored.version, stored.claim), step="released")
        self._claims_at = 0.0
