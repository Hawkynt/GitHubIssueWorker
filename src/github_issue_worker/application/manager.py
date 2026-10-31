"""The manager loop: poll, decide, claim, hand issues to idle workers (F2.7, F3, F11.8, F13).

Each worker slot holds at most one claim, and claims only when it is idle, eligible for the issue,
its account is not limited and below its concurrency cap, so nothing is reserved by a worker that
cannot start it. A configured worker runs as several slots, its instances, when its account allows
several runs at once. A worker
whose run was paused by a usage limit keeps its issue and continues it once the reset has passed.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from github_issue_worker.application.coordinator import ClaimHandle, Coordinator
from github_issue_worker.application.history import RunRecord
from github_issue_worker.application.issue_run import Verdict
from github_issue_worker.application.poller import PollResult, PollState
from github_issue_worker.domain.claim import ClaimState, Holder, RunEntry
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.model_name import display_model
from github_issue_worker.domain.split import (
    SPLIT_LABEL,
    merged_pull,
    parent_of,
    split_finished,
    split_summary,
)
from github_issue_worker.domain.trust import is_trusted
from github_issue_worker.domain.work import (
    SKIP_LABEL,
    RunKind,
    WorkContext,
    new_input,
    pending_run,
)
from github_issue_worker.ports.claims import ClaimStore
from github_issue_worker.ports.clock import Clock
from github_issue_worker.ports.repository import Blocker, RepositoryHost, RepositorySuspended

log = logging.getLogger(__name__)


class Limits(Protocol):
    def limited_until(self, account: str) -> datetime | None: ...
    def set_limited(self, account: str, until: datetime | None) -> None: ...


def instance_name(worker: str, instance: int) -> str:
    """The first instance carries the worker's own name, so its claims survive a resize."""
    return worker if instance == 1 else f"{worker}.{instance}"


@dataclass
class WorkerSlot:
    name: str
    """The instance's name: the worker's name, then ``<worker>.2``, ``<worker>.3``, ..."""
    agent: str
    account: str
    priority: int = 100
    tags: frozenset[str] = frozenset()
    model: str | None = None
    available: bool = True
    observed_model: str | None = None
    """The model the worker's last run reported, which may differ from the configured one."""
    reason: str = ""
    busy: bool = False
    current: tuple[str, int] | None = None
    enabled: bool = True
    """False while paused from the UI: the worker finishes nothing new and claims nothing."""
    merge_conflict: bool = False
    step: str | None = None
    issue_title: str | None = None
    started_at: datetime | None = None
    branch: str | None = None
    log_path: Path | None = None
    """Where the current (or last) agent run of this worker writes its output."""
    worker: str = ""
    """The configured worker this slot is an instance of; empty means the slot's own name."""
    blocker: Blocker | None = None
    """What the current run waits on that only a person can do, such as approving CI."""

    @property
    def group(self) -> str:
        return self.worker or self.name


@dataclass
class RepositoryWatch:
    name: str
    host: RepositoryHost
    coordinator: Coordinator
    store: ClaimStore
    mode: Literal["all", "labeled"] = "all"
    process_backlog: bool = False
    settings: object = None
    """Whatever the run factory needs for this repository (merge method, commands, ...)."""
    suspension_reason: str = ""
    """Empty while enabled; a denial or a failed access query blocks further work."""

    def require_access(self) -> None:
        """Stop a running job before its next outward write, keeping its checkpoint."""
        if self.suspension_reason:
            raise RepositorySuspended(f"{self.name}: {self.suspension_reason}")


RunIssue = Callable[
    [RepositoryWatch, WorkerSlot, ClaimHandle, Issue, tuple[Comment, ...], RunKind], Verdict
]
Poll = Callable[[str, PollState, bool], tuple[PollResult, PollState]]


def _model_of(slot: WorkerSlot) -> str:
    """The model a worker runs, normalised; empty while nobody knows it yet."""
    known = slot.observed_model or slot.model
    return display_model(known) if known else ""


def branch_name(issue: Issue) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", issue.title.lower()).strip("-")[:40].rstrip("-")
    return f"issue/{issue.number}-{slug or 'work'}"


@dataclass
class Manager:
    manager_id: str
    manager_tags: frozenset[str]
    repositories: list[RepositoryWatch]
    workers: list[WorkerSlot]
    poll: Poll
    poll_states: MutableMapping[str, PollState]
    limits: Limits
    clock: Clock
    trusted_users: tuple[str, ...]
    run_issue: RunIssue
    spawn: Callable[[Callable[[], None]], None]
    on_finished: Callable[[RunRecord], None] | None = None
    """Told about every finished run, for the long-term history the cockpit draws."""
    sweep: Callable[[RepositoryWatch, set[int], set[int]], None] | None = None
    """Removes finished branches and worktrees, given the issues held here and held anywhere."""
    sweep_every: timedelta = timedelta(hours=1)
    max_runs: Callable[[str], int] | None = None
    """An account's ``maxConcurrentRuns`` on this manager (F12.11); None leaves it uncapped."""
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _known: dict[tuple[str, int], Issue] = field(default_factory=dict)
    _pending: dict[str, set[int]] = field(default_factory=dict)
    _own: dict[tuple[str, int], str] = field(default_factory=dict)
    _labelled: dict[tuple[str, int], str] = field(default_factory=dict)
    _adopted: set[str] = field(default_factory=set)
    _untrusted_issues: set[tuple[str, int]] = field(default_factory=set)
    _untrusted_comments: set[tuple[str, int]] = field(default_factory=set)
    _swept: dict[str, datetime] = field(default_factory=dict)
    _access_checked: dict[str, tuple[RepositoryWatch, datetime]] = field(
        default_factory=dict, init=False
    )
    _initialized: set[str] = field(default_factory=set, init=False)

    # --- the loop ------------------------------------------------------------------------------

    def start(self) -> None:
        """Check every watch before startup writes, including ``watch --once`` (F1.5)."""
        for watch in self.repositories:
            try:
                self.prepare(watch)
            except Exception:
                log.exception("%s: repository startup failed", watch.name)

    def prepare(self, watch: RepositoryWatch) -> bool:
        """Check access on first use and hourly, even when suspended or idle."""
        now = self.clock.now()
        checked = self._access_checked.get(watch.name)
        last = checked[1] if checked is not None and checked[0] is watch else None
        if last is None:
            self._initialized.discard(watch.name)
        if last is None or now - last >= timedelta(hours=1):
            try:
                allowed = watch.host.info().can_push
            except Exception:
                watch.suspension_reason = "push-access check failed"
                log.exception("%s: push-access check failed; processing suspended", watch.name)
                return False
            self._access_checked[watch.name] = (watch, self.clock.now())
            if not allowed:
                reason = "lost push access"
                if watch.suspension_reason != reason:
                    log.warning("%s: suspended: %s", watch.name, reason)
                watch.suspension_reason = reason
            else:
                if watch.suspension_reason:
                    log.info("%s: push access confirmed; processing resumed", watch.name)
                watch.suspension_reason = ""
        if watch.suspension_reason:
            return False
        if watch.name not in self._initialized:
            watch.host.ensure_worker_labels()
            self._initialized.add(watch.name)
        return True

    def tick(self) -> None:
        for watch in self.repositories:
            try:
                self._tick(watch)
            except Exception:
                log.exception("%s: poll cycle failed", watch.name)

    def _tick(self, watch: RepositoryWatch) -> None:
        if not self.prepare(watch):
            return
        if watch.name not in self._adopted:
            self._adopt(watch)
        state = self.poll_states.get(watch.name, PollState())
        result, state = self.poll(watch.name, state, watch.process_backlog)
        self.poll_states[watch.name] = state
        for issue in result.issues:
            self._known[(watch.name, issue.number)] = issue

        touched = {issue.number for issue in result.issues} | {c.issue for c in result.comments}
        with self._lock:
            own = {number for (repo, number) in self._own if repo == watch.name}
        candidates = touched | self._pending.setdefault(watch.name, set()) | own
        since = state.watched_since or self.clock.now()
        for number in sorted(candidates, key=lambda n: self._updated(watch, n)):
            try:
                self._consider(watch, number, since)
            except Exception:
                log.exception("%s#%d: could not be considered", watch.name, number)
        self._housekeeping(watch)

    def _housekeeping(self, watch: RepositoryWatch) -> None:
        """Every ``sweep_every``, finished issues lose their branch and worktree (F5.5)."""
        now = self.clock.now()
        last = self._swept.get(watch.name)
        if self.sweep is None or (last is not None and now - last < self.sweep_every):
            return
        self._swept[watch.name] = now
        claimed: set[int] = set()
        mine: set[int] = set()
        for number in watch.store.issues():
            stored = watch.store.read(number)
            if stored and stored.claim.state.holds:
                claimed.add(number)
                if stored.claim.holder.manager == self.manager_id:
                    mine.add(number)
        with self._lock:
            mine |= {number for (repo, number) in self._own if repo == watch.name}
            mine |= {s.current[1] for s in self.workers if s.current and s.current[0] == watch.name}
        try:
            self.sweep(watch, mine, claimed)
        except Exception:
            log.exception("%s: housekeeping failed", watch.name)

    def _adopt(self, watch: RepositoryWatch) -> None:
        """Claims this manager still holds from before a restart are continued (F3.4)."""
        for number in watch.store.issues():
            stored = watch.store.read(number)
            if (
                stored
                and stored.claim.state.holds
                and stored.claim.holder.manager == self.manager_id
            ):
                with self._lock:
                    self._own[(watch.name, number)] = stored.claim.holder.worker
        self._adopted.add(watch.name)

    def _updated(self, watch: RepositoryWatch, number: int) -> datetime:
        known = self._known.get((watch.name, number))
        return known.updated_at if known else self.clock.now()

    def _issue(self, watch: RepositoryWatch, number: int) -> Issue:
        key = (watch.name, number)
        if key not in self._known:
            self._known[key] = watch.host.issue(number)
        return self._known[key]

    # --- one issue -----------------------------------------------------------------------------

    def _consider(self, watch: RepositoryWatch, number: int, since: datetime) -> None:
        now = self.clock.now()
        issue = self._issue(watch, number)
        parent = parent_of(issue)
        if issue.state == "closed" and parent is not None:
            self._finish_split(watch, parent)
        elif SPLIT_LABEL in issue.labels:
            self._finish_split(watch, number)
            issue = self._issue(watch, number)
        stored = watch.store.read(number)
        claim = stored.claim if stored else None

        def trusted(actor: Actor) -> bool:
            return is_trusted(actor, watch.host.role(actor.login), self.trusted_users)

        issue_key = (watch.name, number)
        if not trusted(issue.author) and issue_key not in self._untrusted_issues:
            log.info(
                "%s#%d: ignored issue opened by untrusted author %s",
                watch.name,
                number,
                issue.author.login,
            )
            self._untrusted_issues.add(issue_key)

        comments = tuple(watch.host.comments(number))
        for comment in comments:
            comment_key = (watch.name, comment.id)
            if (
                not comment.is_own_output
                and comment_key not in self._untrusted_comments
                and not trusted(comment.author)
            ):
                log.info(
                    "%s#%d: ignored comment %d by untrusted author %s",
                    watch.name,
                    number,
                    comment.id,
                    comment.author.login,
                )
                self._untrusted_comments.add(comment_key)
        context = WorkContext(
            issue, claim, comments, trusted, watch.mode, since, watch.coordinator.policy, now
        )

        if stored and claim and claim.state.holds and claim.holder.manager == self.manager_id:
            self._continue_own(watch, issue, ClaimHandle(stored.version, claim), context)
            return

        kind = pending_run(context)
        if kind is None:
            self._pending[watch.name].discard(number)
            if (watch.name, number) in self._labelled or (
                SKIP_LABEL in issue.labels
                and not (claim and claim.state.holds)
                and any(
                    label.startswith("worker:") and label != SKIP_LABEL for label in issue.labels
                )
            ):
                watch.host.set_worker_label(number, None)
                self._labelled.pop((watch.name, number), None)
            return

        eligible = [slot for slot in self.workers if self._serves(slot, issue)]
        if not eligible:
            self._pending[watch.name].discard(number)
            self._label(watch, number, "worker:unserved")
            return

        with self._lock:
            free = [
                slot
                for slot in eligible
                if self._idle(slot) and not self._limited(slot, now) and self._has_room(slot)
            ]
            if not free:
                self._pending[watch.name].add(number)
                self._label(watch, number, "worker:queued")
                return
            slot = min(free, key=lambda candidate: candidate.priority)
            branch = claim.branch if claim else branch_name(issue)
            handle = watch.coordinator.claim(
                number, Holder(self.manager_id, slot.name), branch, model=_model_of(slot)
            )
            self._pending[watch.name].discard(number)
            if handle is None:
                return
            self._start(watch, slot, handle, issue, new_input(context), kind)

    def _continue_own(
        self, watch: RepositoryWatch, issue: Issue, handle: ClaimHandle, context: WorkContext
    ) -> None:
        claim = handle.claim
        with self._lock:
            slot = next((s for s in self.workers if s.name == claim.holder.worker), None)
            if SKIP_LABEL in issue.labels:
                if slot is None or not slot.busy:
                    watch.host.set_worker_label(issue.number, None)
                    watch.coordinator.release(handle, step="skipped")
                    self._own.pop((watch.name, issue.number), None)
                    self._labelled.pop((watch.name, issue.number), None)
                return
            self._own[(watch.name, issue.number)] = claim.holder.worker
            if slot is None or not slot.available or slot.busy:
                return
            if claim.state is ClaimState.PAUSED:
                if claim.paused_until and claim.paused_until > self.clock.now():
                    return
                handle = watch.coordinator.resume(handle)
            self._start(watch, slot, handle, issue, new_input(context), RunKind.CONTINUATION)

    def _finish_split(self, watch: RepositoryWatch, number: int) -> None:
        """Close a split parent once every sub-issue is completed (F4.6)."""
        parent = watch.host.issue(number)
        self._known[(watch.name, number)] = parent
        if parent.state != "open" or SPLIT_LABEL not in parent.labels:
            return
        children = watch.host.sub_issues(number)
        if not split_finished(children):
            return
        pulls = {
            child.number: pull
            for child in children
            if (pull := merged_pull(watch.host.comments(child.number)))
        }
        watch.host.comment(number, split_summary(children, pulls))
        watch.host.close_issue(number)
        watch.host.set_worker_label(number, None)
        self._labelled.pop((watch.name, number), None)
        log.info("%s#%d: every sub-issue is done, closed", watch.name, number)

    # --- workers -------------------------------------------------------------------------------

    def _serves(self, slot: WorkerSlot, issue: Issue) -> bool:
        if not slot.available or not slot.enabled:
            return False
        for label in issue.labels:
            if label.startswith("agent:") and label.removeprefix("agent:") != slot.agent:
                return False
            if label.startswith("needs:") and label.removeprefix("needs:") not in (
                slot.tags | self.manager_tags
            ):
                return False
        return True

    def _idle(self, slot: WorkerSlot) -> bool:
        return not slot.busy and slot.name not in self._own.values()

    def _has_room(self, slot: WorkerSlot) -> bool:
        """Below the account's cap, counting every slot on it that runs or holds a claim."""
        if self.max_runs is None:
            return True
        holding = set(self._own.values())
        used = sum(
            1
            for other in self.workers
            if other.account == slot.account and (other.busy or other.name in holding)
        )
        return used < self.max_runs(slot.account)

    def _limited(self, slot: WorkerSlot, now: datetime) -> bool:
        until = self.limits.limited_until(slot.account)
        return until is not None and until > now

    def _start(
        self,
        watch: RepositoryWatch,
        slot: WorkerSlot,
        handle: ClaimHandle,
        issue: Issue,
        comments: tuple[Comment, ...],
        kind: RunKind,
    ) -> None:
        slot.busy = True
        slot.current = (watch.name, issue.number)
        slot.issue_title = issue.title
        slot.started_at = self.clock.now()
        slot.branch = handle.claim.branch
        slot.step = None
        slot.merge_conflict = handle.claim.merge_conflict
        self._own[(watch.name, issue.number)] = slot.name
        self._labelled.pop((watch.name, issue.number), None)
        log.info(
            "%s#%d: %s run by %s on %s",
            watch.name,
            issue.number,
            kind,
            slot.name,
            display_model(slot.observed_model or slot.model)
            if (slot.observed_model or slot.model)
            else "its default model",
        )

        started = self.clock.now()

        def job() -> None:
            verdict = Verdict("failed", detail="the run crashed")
            crashed = False
            try:
                verdict = self.run_issue(watch, slot, handle, issue, comments, kind)
            except RepositorySuspended as suspended:
                verdict = Verdict("suspended", detail=str(suspended))
            except Exception:
                # The claim stays held by this worker and is continued on the next tick.
                log.exception("%s#%d: run crashed; the claim is kept", watch.name, issue.number)
                crashed = True
            finally:
                with self._lock:
                    if crashed or verdict.kind == "suspended":
                        pass
                    elif verdict.kind == "paused":
                        self.limits.set_limited(slot.account, verdict.until)
                    else:
                        self._own.pop((watch.name, issue.number), None)
                    slot.busy = False
                    slot.current = None
                    slot.issue_title = None
                    slot.started_at = None
                    slot.branch = None
                    slot.step = None
                    slot.blocker = None
                self._record(
                    watch, slot, issue, kind, "crashed" if crashed else verdict.kind, started
                )
                log.info(
                    "%s#%d: %s by %s on %s %s",
                    watch.name,
                    issue.number,
                    verdict.kind,
                    slot.name,
                    display_model(slot.observed_model or slot.model)
                    if (slot.observed_model or slot.model)
                    else "its default model",
                    verdict.detail,
                )

        self.spawn(job)

    def _record(
        self,
        watch: RepositoryWatch,
        slot: WorkerSlot,
        issue: Issue,
        kind: RunKind,
        verdict: str,
        started: datetime,
    ) -> None:
        model = _model_of(slot) or "unknown model"
        finished = self.clock.now()
        try:
            # In the claim, so every manager of the fleet counts the run the same way.
            watch.require_access()
            watch.coordinator.record_run(
                issue.number,
                RunEntry(
                    self.manager_id,
                    slot.name,
                    slot.agent,
                    model,
                    kind.value,
                    verdict,
                    started,
                    finished,
                ),
            )
        except RepositorySuspended:
            pass  # The local history is published after access returns.
        except Exception:
            log.exception(
                "%s#%d: the run could not be recorded in its claim", watch.name, issue.number
            )
        if self.on_finished is None:
            return
        try:
            self.on_finished(
                RunRecord(
                    repository=watch.name,
                    issue=issue.number,
                    worker=slot.name,
                    agent=slot.agent,
                    model=model,
                    kind=kind.value,
                    verdict=verdict,
                    started=started,
                    finished=finished,
                    issue_started=started,
                    manager=self.manager_id,
                )
            )
        except Exception:
            log.exception("%s#%d: the run could not be recorded", watch.name, issue.number)

    def _label(self, watch: RepositoryWatch, number: int, label: str) -> None:
        if self._labelled.get((watch.name, number)) != label:
            watch.host.set_worker_label(number, label)
            self._labelled[(watch.name, number)] = label

    # --- reporting -----------------------------------------------------------------------------

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "worker": slot.name,
                    "agent": slot.agent,
                    "account": slot.account,
                    "available": slot.available,
                    "reason": slot.reason,
                    "busy": slot.busy,
                    "current": f"{slot.current[0]}#{slot.current[1]}" if slot.current else None,
                    "limitedUntil": self.limits.limited_until(slot.account),
                }
                for slot in self.workers
            ]
