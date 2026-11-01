"""One worker's run on one claimed issue, from the first agent step to a verdict (F2.7, F6-F9).

The agent works; this module checks and ships. Nothing reaches GitHub before the claim has been
fenced, the work verified and every commit passed the commit gate.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Literal

from github_issue_worker.application.coordinator import ClaimHandle, ClaimLost, Coordinator
from github_issue_worker.application.prompt import build_prompt
from github_issue_worker.domain.commit_message import CommitGate, change_lines
from github_issue_worker.domain.issue import Comment, Issue
from github_issue_worker.domain.outcome import Outcome
from github_issue_worker.domain.run_limit import park_comment
from github_issue_worker.domain.split import (
    SPLIT_LABEL,
    SPLIT_STEP,
    inherited_labels,
    missing_sub_issues,
    parent_of,
    sub_issue_body,
)
from github_issue_worker.domain.work import SKIP_LABEL, RunKind
from github_issue_worker.ports.agents import AgentRun, RunReport
from github_issue_worker.ports.clock import Clock
from github_issue_worker.ports.github import GitHubError, RateLimited
from github_issue_worker.ports.repository import (
    Blocker,
    Check,
    PullRequest,
    RepositoryHost,
    RepositorySuspended,
    WaitingRun,
)
from github_issue_worker.ports.workspace import WorkspacePort
from github_issue_worker.product import MARKER_PREFIX

VerdictKind = Literal[
    "merged",
    "needs-input",
    "awaiting-review",
    "paused",
    "failed",
    "lost",
    "stopped",
    "skipped",
    "split",
    "suspended",
]


def checkpoint_and_release(
    host: RepositoryHost,
    workspace: WorkspacePort,
    coordinator: Coordinator,
    handle: ClaimHandle,
    repository: str,
    base: str,
) -> None:
    """The shipping path for removal: checkpoint before freeing a stopped run (F1.4)."""
    handle = coordinator.fence(handle)
    claim = handle.claim
    workspace.checkpoint(repository, claim.issue, claim.branch, base)
    handle = coordinator.fence(handle)
    host.set_worker_label(claim.issue, "worker:queued")
    coordinator.release(handle, step="released")


class RepositoryRemoved(Exception):
    """The run stops; its manager checkpoints and releases after the run has exited."""


class RunStopped(Exception):
    """The maintainer stopped the agent (UI: stop & release). Not a failure to retry."""


_MERGEABLE = frozenset({"clean", "unstable", "has_hooks"})
UNDERSTANDING_MARKER = f"{MARKER_PREFIX}understanding -->"
_UNDERSTANDING_HEADER = "**How I understand this issue**"
_UNDERSTANDING_FOOTER = (
    "This is what the work will be measured against. If it is not what you meant, say so in a "
    "comment and the work adapts."
)


LINK_MARKER = f"{MARKER_PREFIX}link -->"


def understanding_in(comments: list[Comment]) -> str | None:
    """The latest restatement a manager posted to the issue, without its frame."""
    for comment in reversed(comments):
        if UNDERSTANDING_MARKER in comment.body:
            text = comment.body.replace(UNDERSTANDING_MARKER, "")
            text = text.replace(_UNDERSTANDING_HEADER, "").replace(_UNDERSTANDING_FOOTER, "")
            return text.strip() or None
    return None


_REBASE = frozenset({"dirty", "behind"})
_FAILED = frozenset({"failure", "timed_out", "cancelled", "action_required", "startup_failure"})
_NOT_STARTED = ("job was not started", "spending limit", "billing")
"""Signs that CI never ran the job, which no change to the code can repair (seen live)."""


def _described(run: WaitingRun) -> str:
    """ "CI's pull request run": which of the workflow's runs waits, by what started it."""
    kind = {"pull_request": "pull request", "workflow_dispatch": "dispatched", "push": "push"}
    return f"{run.name}'s {kind.get(run.event, run.event or 'held')} run"


def _never_started(check: Check) -> bool:
    text = " ".join(check.annotations).lower()
    return any(sign in text for sign in _NOT_STARTED)


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: VerdictKind
    until: datetime | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RunSettings:
    worker: str = "codex"
    model: str | None = None
    merge_method: str = "squash"
    base_branch: str | None = None
    ci_repair_attempts: int = 3
    corrections: int = 2
    """Rounds of feedback for failed verification, the commit gate or missing commits."""
    checks_timeout: timedelta = timedelta(hours=2)
    check_interval: timedelta = timedelta(seconds=30)
    limit_probe: timedelta = timedelta(minutes=5)
    understand_first: bool = True
    """First and reopen runs start with a run that restates the issue and posts it (F4.5)."""
    split_issues: bool = True
    """The understanding run may split a large issue with independent parts (F4.6)."""
    create_missing_labels: bool = False
    """Triage may create a label the repository does not have (F4.2); off, they are dropped."""
    gate: CommitGate = field(default_factory=CommitGate)


class _Stop(Exception):
    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict


@dataclass
class IssueRun:
    host: RepositoryHost
    workspace: WorkspacePort
    coordinator: Coordinator
    agent: Callable[[AgentRun], RunReport]
    verify: Callable[[Path], tuple[bool, str]]
    sessions: MutableMapping[tuple[int, str], str]
    clock: Clock
    sleep: Callable[[float], None]
    settings: RunSettings
    progress: Callable[[str], None] = lambda step: None
    """Told each step the run enters: understand, implement, verify, push, checks, merge."""
    blocked: Callable[[Blocker | None], None] = lambda blocker: None
    """Told when the run waits on something only a person can do, and when that is over."""

    conflict_changed: Callable[[bool], None] = lambda conflict: None
    cancelled: Callable[[], bool] = lambda: False

    def run(
        self, handle: ClaimHandle, issue: Issue, comments: tuple[Comment, ...], kind: RunKind
    ) -> Verdict:
        self._handle = handle
        self._issue = issue
        self.conflict_changed(handle.claim.merge_conflict)
        self._consumed = max((comment.id for comment in comments), default=None)
        self._understanding: str | None = None
        self._linked = False
        try:
            return self._run(issue, comments, kind)
        except RepositorySuspended as suspended:
            return Verdict("suspended", detail=str(suspended))
        except ClaimLost as lost:
            return Verdict("lost", detail=str(lost))
        except RepositoryRemoved:
            return Verdict("stopped", detail="repository removed")
        except RunStopped as stopped:
            if self.cancelled():
                return Verdict("stopped", detail="repository removed")
            self.host.set_worker_label(issue.number, "worker:queued")
            self.coordinator.release(self._handle, step="stopped")
            return Verdict("stopped", detail=str(stopped))
        except _Stop as stop:
            return stop.verdict

    def _check_cancelled(self) -> None:
        if self.cancelled():
            raise RepositoryRemoved

    def _progress(self, step: str) -> None:
        self._check_cancelled()
        self._handle = self.coordinator.renew(self._handle, step=step)
        self.progress(step)

    # --- the run ---------------------------------------------------------------------------------

    def _run(self, issue: Issue, comments: tuple[Comment, ...], kind: RunKind) -> Verdict:
        repo = self.host.repository
        branch = self._handle.claim.branch
        self._fenced(lambda: self.host.set_worker_label(issue.number, "worker:working"))
        base = self.settings.base_branch or self.host.info().default_branch
        tree = self.workspace.worktree(
            repo, issue.number, branch, base, resume=kind not in (RunKind.FIRST, RunKind.REOPEN)
        )
        label_choices = None
        if not {name for name in issue.labels if not name.startswith("worker")}:
            label_choices = self.host.label_names() - {
                n for n in self.host.label_names() if n.startswith("worker")
            }

        self._understanding = understanding_in(self.host.comments(issue.number))
        if (
            self._understanding is None
            and self.settings.understand_first
            and kind in (RunKind.FIRST, RunKind.REOPEN)
        ):
            self._checkpoint("understand")
            verdict = self._understand(tree, base, issue, comments, kind, branch, label_choices)
            if verdict is not None:
                return verdict
            label_choices = None

        pushed = self.workspace.remote_head(repo, branch)
        feedback: str | None = None
        corrections = errors = repairs = 0
        outcome: Outcome | None = None
        pull: PullRequest | None = None

        while True:
            if pull is None or feedback is not None:
                self._checkpoint("implement")
                report = self._agent(tree, issue, comments, kind, branch, label_choices, feedback)
                if report.limit is not None:
                    return self._pause(issue, report)
                if report.cut_off is not None:
                    return self._park(issue, tree, base, report)
                if report.outcome is None:
                    errors += 1
                    if errors > 1:
                        return self._fail(issue, report.error or "the agent produced no outcome")
                    feedback = f"The run ended without a usable outcome: {report.error}"
                    continue
                outcome = report.outcome
                if label_choices and outcome.labels:
                    self._apply_labels(issue, outcome.labels, label_choices)
                    label_choices = None
                if outcome.kind != "done":
                    return self._ask(issue, outcome)

                self._checkpoint("verify")
                problem = self._check(tree, base)
                if problem:
                    corrections += 1
                    if corrections > self.settings.corrections:
                        return self._fail(issue, problem)
                    feedback = problem
                    continue

                self._checkpoint("push")
                if not self._linked:
                    pushed = self._link(issue, tree, branch, base, pushed)
                pushed = self._push(tree, branch, pushed)
                if pull is None:
                    pull = self.host.find_pull_request(branch) or self._open(
                        issue, tree, base, branch, outcome
                    )
                feedback = None

            self._checkpoint("checks")
            state, detail = self._await(pull, issue)
            if state == "merge":
                self._checkpoint("merge")
                return self._merge(issue, pull, branch, tree, base)
            if state == "rebase":
                if self.workspace.rebase(tree, base):
                    problem = self._check(tree, base)
                    if not problem:
                        pushed = self._push(tree, branch, pushed, force=True)
                        continue
                    detail = problem
                else:
                    self._conflict(True)
                    detail = f"The branch conflicts with `{base}`: rebase onto origin/{base}."
            if state == "timeout":
                return self._awaiting(issue, pull)
            if state == "blocked":
                return self._awaiting(issue, pull, reason=detail)
            repairs += 1
            if repairs > self.settings.ci_repair_attempts:
                return self._fail(
                    issue, f"still failing after {repairs - 1} repair attempts: {detail}"
                )
            feedback = detail

    # --- steps -----------------------------------------------------------------------------------

    def _apply_labels(self, issue: Issue, proposed: tuple[str, ...], choices: set[str]) -> None:
        """Apply the labels the run chose. With ``createMissingLabels`` on, a label the
        repository lacks is created first; off, it is dropped (F4.2). The ``worker:*``
        namespace stays the manager's own and is never created from an outcome."""
        chosen = [label for label in proposed if label in choices]
        if self.settings.create_missing_labels:
            for label in proposed:
                if label in choices or label.startswith("worker:"):
                    continue
                self._fenced(partial(self.host.create_label, label))
                chosen.append(label)
        if chosen:
            self._fenced(partial(self.host.add_labels, issue.number, chosen))

    def _understand(
        self,
        tree: Path,
        base: str,
        issue: Issue,
        comments: tuple[Comment, ...],
        kind: RunKind,
        branch: str,
        label_choices: set[str] | None,
    ) -> Verdict | None:
        """Restate the issue before any work, apply the labels, post the restatement (F4.5), and
        split it into sub-issues when the agent found independent parts (F4.6)."""
        may_split = self.settings.split_issues and parent_of(issue) is None
        feedback: str | None = None
        for _ in range(2):
            report = self._agent(
                tree,
                issue,
                comments,
                kind,
                branch,
                label_choices,
                feedback,
                phase="understand",
                may_split=may_split,
            )
            if report.limit is not None:
                return self._pause(issue, report)
            if report.cut_off is not None:
                return self._park(issue, tree, base, report)
            outcome = report.outcome
            if outcome is not None and outcome.kind != "done":
                return self._ask(issue, outcome)
            parts = outcome.sub_issues if outcome is not None and may_split else ()
            if outcome is not None and outcome.restatement and len(parts) == 1:
                feedback = (
                    "A split needs at least two independent parts. Either name every part in "
                    "`subIssues`, or leave `subIssues` empty and the issue is worked as a whole."
                )
                continue
            if outcome is not None and outcome.restatement:
                if parts:
                    self._create_sub_issues(issue, outcome)
                if label_choices and outcome.labels:
                    self._apply_labels(issue, outcome.labels, label_choices)
                body = (
                    f"{_UNDERSTANDING_HEADER}\n\n{outcome.restatement}\n\n"
                    f"{_UNDERSTANDING_FOOTER}\n\n{UNDERSTANDING_MARKER}"
                )
                self._fenced(partial(self.host.comment, issue.number, body))
                self._understanding = outcome.restatement
                return self._split(issue, branch) if parts else None
            reason = report.error if outcome is None else "the outcome has no restatement"
            feedback = (
                f"The understanding run is not usable: {reason}. Write `.ghiw/outcome.json` with "
                "a `restatement` of the issue as described below."
            )
        return self._fail(issue, f"the issue could not be restated: {feedback}")

    def _agent(
        self,
        tree: Path,
        issue: Issue,
        comments: tuple[Comment, ...],
        kind: RunKind,
        branch: str,
        label_choices: set[str] | None,
        feedback: str | None,
        phase: str = "implement",
        may_split: bool = False,
    ) -> RunReport:
        self._check_cancelled()
        key = (issue.number, self.settings.worker)
        prompt = build_prompt(
            self.host.repository,
            branch,
            issue,
            comments,
            kind,
            label_choices,
            feedback,
            understanding=self._understanding,
            phase=phase,
            may_split=may_split,
        )
        report = self.agent(
            AgentRun(
                worktree=tree,
                prompt=prompt,
                session=self.sessions.get(key),
                model=self.settings.model,
            )
        )
        self._check_cancelled()
        if report.session:
            self.sessions[key] = report.session
        self._handle = self.coordinator.renew(self._handle)
        self._checkpoint()
        return report

    def _check(self, tree: Path, base: str) -> str | None:
        """Why the work is not done yet, or None: verification, then commits, then the gate."""
        self._check_cancelled()
        ok, output = self.verify(tree)
        self._checkpoint()
        if not ok:
            return "The build or tests fail:\n\n" + output[-6000:]
        commits = self.workspace.commits(tree, base)
        if not commits:
            return "There is no commit on the branch yet. Commit the work following the rules."
        problems = []
        for commit in commits:
            for violation in self.settings.gate.check(commit.message):
                where = f"{commit.sha[:10]} line {violation.line}"
                problems.append(f"- {where}: {violation.rule}, {violation.detail}")
        if problems:
            return (
                "These commits break the commit rules. Rewrite them (for example with an "
                "interactive-free `git rebase` and `git commit --amend`) and keep the work:\n\n"
                + "\n".join(problems)
            )
        return None

    def _link(
        self, issue: Issue, tree: Path, branch: str, base: str, pushed: str | None
    ) -> str | None:
        """Link the branch to the issue before its first push (F5.3); returns the remote head the
        push leases against. ``createLinkedBranch`` only creates a branch, so a new one is created
        linked at the base commit and the push fast-forwards it; an existing unlinked one is kept
        and linking it by hand is asked for once."""
        if branch in self.host.linked_branches(issue.number):
            self._linked = True
            return pushed
        if pushed is None:
            oid = self.workspace.fork_point(tree, base)
            try:
                self._fenced(partial(self.host.link_branch, issue.number, branch, oid))
            except RateLimited:
                raise
            except GitHubError as error:
                if error.status >= 500:
                    raise  # GitHub's trouble, not the issue's: the run is retried
                reason = f"the branch `{branch}` could not be linked to the issue: {error}"
                raise _Stop(self._fail(issue, reason)) from error
            self._linked = True
            return oid
        if not any(LINK_MARKER in comment.body for comment in self.host.comments(issue.number)):
            text = (
                f"The branch `{branch}` existed before it could be linked to this issue, and "
                "GitHub links only a branch it creates itself, so it is kept as it is. Link it by "
                "hand in the issue's *Development* panel to see it there; the work goes on "
                f"meanwhile.\n\n{LINK_MARKER}"
            )
            self._fenced(partial(self.host.comment, issue.number, text))
        self._linked = True
        return pushed

    def _push(self, tree: Path, branch: str, expected: str | None, force: bool = False) -> str:
        self._checkpoint()
        self._handle = self.coordinator.fence(self._handle)
        return self.workspace.push(self.host.repository, tree, branch, expected)

    def _open(
        self, issue: Issue, tree: Path, base: str, branch: str, outcome: Outcome
    ) -> PullRequest:
        scenarios = "\n".join(f"- {scenario}" for scenario in outcome.scenarios)
        body = "\n".join(self._changes(tree, base))
        if scenarios:
            body += f"\n\nScenarios:\n{scenarios}"
        body += f"\n\nCloses #{issue.number}"
        self._title = self._subject(issue, tree, base)
        self._checkpoint()
        self._handle = self.coordinator.fence(self._handle)
        return self.host.create_pull_request(branch, base, self._title, body)

    def _subject(self, issue: Issue, tree: Path, base: str) -> str:
        """The issue's title behind the prefix of the branch's first commit."""
        commits = self.workspace.commits(tree, base)
        prefix = commits[0].message[:1] if commits else "*"
        return f"{prefix} {issue.title}"

    def _changes(self, tree: Path, base: str) -> list[str]:
        """The branch's change lines, oldest commit first: what the PR, the squash commit and the
        closing comment say, instead of prose."""
        found: list[str] = []
        for commit in self.workspace.commits(tree, base):
            found += [line for line in change_lines(commit.message) if line not in found]
        return found

    def _await(self, pull: PullRequest, issue: Issue) -> tuple[str, str]:
        """Wait until the pull request can merge, needs a rebase, fails its checks or times out."""
        try:
            return self._await_checks(pull)
        finally:
            self.blocked(None)

    def _await_checks(self, pull: PullRequest) -> tuple[str, str]:
        deadline = self.clock.now() + self.settings.checks_timeout
        while True:
            self._handle = self.coordinator.fence(self._handle)
            self._checkpoint()
            current = self.host.pull_request(pull.number)
            if current.mergeable_state == "dirty":
                self._conflict(True)
            elif (
                current.merged
                or current.mergeable_state in _MERGEABLE
                or current.mergeable_state in ("behind", "blocked")
            ):
                self._conflict(False)
            if current.merged or current.mergeable_state in _MERGEABLE:
                return "merge", ""
            if current.mergeable_state in _REBASE:
                return "rebase", ""
            failing = [c for c in self.host.checks(current.head_sha) if c.conclusion in _FAILED]
            if failing and all(_never_started(check) for check in failing):
                return "blocked", failing[0].annotations[0]
            if failing:
                lines = [
                    f"- {check.name}: {check.conclusion}\n{check.summary}" for check in failing
                ]
                return "ci", "The pull request's checks fail:\n\n" + "\n".join(lines)
            if self.clock.now() >= deadline:
                return "timeout", ""
            waiting = self._report_approvals(current)
            # Someone may approve any moment: look again soon, so the blocker goes as soon as
            # the runs start.
            interval = self.settings.check_interval.total_seconds()
            self.sleep(min(interval, 10.0) if waiting else interval)

    def _conflict(self, conflict: bool) -> None:
        self._check_cancelled()
        self._handle = self.coordinator.set_merge_conflict(self._handle, conflict)
        self.conflict_changed(conflict)

    def _report_approvals(self, current: PullRequest) -> bool:
        """CI that GitHub holds back for approval would wait silently; say so where it shows."""
        waiting = self.host.waiting_for_approval(current.head_sha)
        # Runs of this repository's own branch (the manager's pushes, the screenshot bot) are
        # approved here; only a fork's need a person.
        for run in waiting:
            if run.own:
                self._fenced(partial(self.host.approve_run, run.id))
        waiting = [run for run in waiting if not run.own]
        if not waiting:
            self.blocked(None)
            return False
        names = ", ".join(sorted({_described(run) for run in waiting}))
        self.blocked(
            Blocker(
                reason=f"{names} {'waits' if len(waiting) == 1 else 'wait'} for approval to run; "
                "the merge needs it, whatever else already runs",
                url=waiting[0].url or current.url,
                approvals=tuple(run.id for run in waiting),
            )
        )
        return True

    def _merge(
        self, issue: Issue, pull: PullRequest, branch: str, tree: Path, base: str
    ) -> Verdict:
        current = self.host.pull_request(pull.number)
        if current.mergeable_state == "dirty":
            self._conflict(True)
        changes = "\n".join(self._changes(tree, base))
        # The PR's own title carries the change-line prefix, also when another run opened it.
        subject = current.title or getattr(self, "_title", "") or self._subject(issue, tree, base)
        title = f"{subject} (#{pull.number})"
        self._check_cancelled()
        self._handle = self.coordinator.fence(self._handle)
        merge_sha = (
            ""
            if current.merged
            else self.host.merge(
                pull.number, self.settings.merge_method, current.head_sha, title, changes
            )
        )
        self.host.delete_branch(branch)
        if self.host.issue(issue.number).state != "closed":
            self.host.close_issue(issue.number)
        self.host.comment(
            issue.number, f"Merged in {current.url} ({merge_sha or 'already merged'}).\n\n{changes}"
        )
        self.host.set_worker_label(issue.number, None)
        self.coordinator.complete(self._handle, consumed=self._consumed)
        self.workspace.remove(self.host.repository, issue.number, branch)
        return Verdict("merged", detail=current.url)

    def _ask(self, issue: Issue, outcome: Outcome) -> Verdict:
        lines = [outcome.summary, ""]
        for number, question in enumerate(outcome.questions, start=1):
            lines.append(f"**{number}. {question.text}**")
            lines += [f"- {option}" for option in question.options]
            if question.recommendation:
                lines.append(f"\nRecommendation: {question.recommendation}")
            lines.append("")
        lines.append("Answer in a comment and the work continues.")
        self._fenced(lambda: self.host.comment(issue.number, "\n".join(lines)))
        self.host.set_worker_label(issue.number, "worker:needs-input")
        self.coordinator.release(self._handle, step="needs-input", consumed=self._consumed)
        return Verdict(
            outcome.kind if outcome.kind == "needs-input" else "needs-input", detail=outcome.summary
        )

    def _park(self, issue: Issue, tree: Path, base: str, report: RunReport) -> Verdict:
        """The run hit `runTimeout` or its budget (F6.7): another run would spend the same limit
        again, so the issue waits for a person, told what was finished and what was not."""
        assert report.cut_off is not None
        subjects = [
            (commit.message.splitlines() or [""])[0]
            for commit in self.workspace.commits(tree, base)
        ]
        body = park_comment(
            report.cut_off,
            commits=[subject for subject in subjects if subject.strip()],
            journal=self.workspace.journal(tree),
            cost_usd=report.usage.cost_usd if report.usage else None,
        )
        self._fenced(lambda: self.host.comment(issue.number, body))
        self.host.set_worker_label(issue.number, "worker:needs-input")
        self.coordinator.release(self._handle, step="needs-input", consumed=self._consumed)
        return Verdict("needs-input", detail=body.splitlines()[0])

    def _create_sub_issues(self, issue: Issue, outcome: Outcome) -> None:
        """One queued sub-issue per part. Parts that already exist, from a run that crashed half
        way, are reused by title, so a retry creates no twin."""
        labels = [*inherited_labels(issue.labels), "worker:queued"]
        existing = self.host.sub_issues(issue.number)
        for part in missing_sub_issues(outcome.sub_issues, existing):
            body = sub_issue_body(issue.number, part)
            self._fenced(
                partial(self.host.create_sub_issue, issue.number, part.title, body, labels)
            )

    def _split(self, issue: Issue, branch: str) -> Verdict:
        """The parent waits for its sub-issues and holds nothing; nothing is implemented on it."""
        children = self.host.sub_issues(issue.number)
        lines = ["Split into sub-issues that are worked on their own:", ""]
        lines += [f"- #{child.number} {child.title}" for child in children]
        self._fenced(partial(self.host.comment, issue.number, "\n".join(lines)))
        self.host.set_worker_label(issue.number, SPLIT_LABEL)
        self.coordinator.release(self._handle, step=SPLIT_STEP, consumed=self._consumed)
        self.workspace.remove(self.host.repository, issue.number, branch)
        return Verdict("split", detail=", ".join(f"#{child.number}" for child in children))

    def _pause(self, issue: Issue, report: RunReport) -> Verdict:
        assert report.limit is not None
        until = report.limit.reset_at or self.clock.now() + self.settings.limit_probe
        self._handle = self.coordinator.pause(
            self._handle, until=until, reason=report.limit.message
        )
        self.host.set_worker_label(issue.number, "worker:paused")
        return Verdict("paused", until=until, detail=report.limit.message)

    def _awaiting(self, issue: Issue, pull: PullRequest, reason: str | None = None) -> Verdict:
        if reason:
            text = (
                f"{pull.url} is ready, but CI could not run, so nothing in the code can fix it: "
                f"{reason}\n\nOnce CI runs again, comment here and the work continues."
            )
        else:
            text = (
                f"{pull.url} is ready, but the repository's rules block the merge (a required "
                "review or required checks). It merges once they are satisfied and the issue "
                "gets a new comment or approval."
            )
        self._fenced(lambda: self.host.comment(issue.number, text))
        self.host.set_worker_label(issue.number, "worker:awaiting-review")
        self.coordinator.release(self._handle, step="awaiting-review", consumed=self._consumed)
        return Verdict("awaiting-review", detail=pull.url)

    def _fail(self, issue: Issue, reason: str) -> Verdict:
        self._fenced(lambda: self.host.comment(issue.number, f"I gave up on this issue: {reason}"))
        self.host.set_worker_label(issue.number, "worker:failed")
        self.coordinator.release(self._handle, step="failed", consumed=self._consumed)
        return Verdict("failed", detail=reason)

    def _checkpoint(self, step: str | None = None) -> None:
        """Stop at a checkpoint for repository removal or ``worker:skip`` (F1.4, F2.7)."""
        self._check_cancelled()
        issue = self._issue.number
        if SKIP_LABEL in self._issue.labels or SKIP_LABEL in self.host.issue(issue).labels:
            self._handle = self.coordinator.fence(self._handle)
            self.host.set_worker_label(issue, None)
            self._handle = self.coordinator.release(self._handle, step="skipped")
            raise _Stop(Verdict("skipped", detail="labelled worker:skip"))
        if step is not None:
            self._progress(step)

    def _fenced(self, write: Callable[[], object]) -> None:
        self._checkpoint()
        self._handle = self.coordinator.fence(self._handle)
        write()
