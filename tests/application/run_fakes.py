"""In-memory host, workspace and agent for issue-run tests."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from github_issue_worker.adapters.workspace import CommitInfo, PushRejected
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.ports.agents import AgentRun, RunReport
from github_issue_worker.ports.repository import Check, PullRequest, RepositoryInfo, WaitingRun

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@dataclass
class FakeHost:
    repository: str = "Hawkynt/ShiftDiff"
    issues: dict[int, Issue] = field(default_factory=dict)
    issue_comments: list[Comment] = field(default_factory=list)
    repo_labels: set[str] = field(default_factory=lambda: {"bug", "enhancement"})
    worker_label: dict[int, str | None] = field(default_factory=dict)
    added_labels: dict[int, list[str]] = field(default_factory=dict)
    created_labels: list[str] = field(default_factory=list)
    posted: list[tuple[int, str]] = field(default_factory=list)
    pulls: dict[int, PullRequest] = field(default_factory=dict)
    pull_bodies: dict[int, tuple[str, str]] = field(default_factory=dict)
    states: list[str] = field(default_factory=lambda: ["clean"])
    """mergeable_state returned by successive pull_request() calls; the last one repeats."""
    check_results: list[list[Check]] = field(default_factory=lambda: [[]])
    merged: list[tuple[int, str, str]] = field(default_factory=list)
    merge_messages: list[tuple[str, str]] = field(default_factory=list)
    deleted_branches: list[str] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    head_of: dict[str, str] = field(default_factory=dict)
    children: dict[int, list[int]] = field(default_factory=dict)
    remote_branches: list[str] = field(default_factory=list)
    branch_pulls: dict[str, list[PullRequest]] = field(default_factory=dict)
    approvals: list[list[WaitingRun]] = field(default_factory=lambda: [[]])
    """Runs waiting for approval in successive calls; the last answer repeats."""
    approved: list[int] = field(default_factory=list)
    """Sub-issue numbers per parent, in the order they were attached."""

    def info(self) -> RepositoryInfo:
        return RepositoryInfo("main", True, ("squash", "merge"))

    def role(self, login: str) -> str | None:
        return "admin"

    def issue(self, number: int) -> Issue:
        issue = self.issues[number]
        if number in self.closed:
            return replace(issue, state="closed", state_reason="completed")
        return issue

    def open_issues(self) -> list[Issue]:
        return [issue for number, issue in self.issues.items() if number not in self.closed]

    def comments(self, number: int) -> list[Comment]:
        return [comment for comment in self.issue_comments if comment.issue == number]

    def label_names(self) -> set[str]:
        return set(self.repo_labels)

    def ensure_worker_labels(self) -> None:
        pass

    def set_worker_label(self, issue: int, label: str | None) -> None:
        self.worker_label[issue] = label

    def add_labels(self, issue: int, labels: list[str]) -> None:
        self.added_labels.setdefault(issue, []).extend(labels)

    def create_label(self, name: str) -> None:
        self.created_labels.append(name)
        self.repo_labels.add(name)

    def comment(self, issue: int, body: str) -> None:
        self.posted.append((issue, body))

    def find_pull_request(self, branch: str) -> PullRequest | None:
        for pull in self.pulls.values():
            if pull.number in self.pull_bodies and self.pull_bodies[pull.number][0] == branch:
                return self.pull_request(pull.number)
        return None

    def create_pull_request(self, branch: str, base: str, title: str, body: str) -> PullRequest:
        number = len(self.pulls) + 100
        self.pulls[number] = PullRequest(
            number, self.head_of[branch], f"https://x/pull/{number}", "unknown", title=title
        )
        self.pull_bodies[number] = (branch, f"{title}\n{body}")
        return self.pulls[number]

    def pull_request(self, number: int) -> PullRequest:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        branch = self.pull_bodies[number][0]
        return replace(self.pulls[number], mergeable_state=state, head_sha=self.head_of[branch])

    def checks(self, sha: str) -> list[Check]:
        return self.check_results.pop(0) if len(self.check_results) > 1 else self.check_results[0]

    def merge(self, number: int, method: str, sha: str, title: str = "", message: str = "") -> str:
        self.merged.append((number, method, sha))
        self.merge_messages.append((title, message))
        return "merge-sha"

    def delete_branch(self, branch: str) -> None:
        self.deleted_branches.append(branch)
        if branch in self.remote_branches:
            self.remote_branches.remove(branch)

    def waiting_for_approval(self, sha: str) -> list[WaitingRun]:
        return self.approvals.pop(0) if len(self.approvals) > 1 else self.approvals[0]

    def approve_run(self, run: int) -> None:
        self.approved.append(run)

    def branches(self, prefix: str) -> list[str]:
        return [branch for branch in self.remote_branches if branch.startswith(prefix)]

    def pull_requests_for(self, branch: str) -> list[PullRequest]:
        return list(self.branch_pulls.get(branch, []))

    def close_issue(self, issue: int) -> None:
        self.closed.append(issue)

    def create_sub_issue(self, parent: int, title: str, body: str, labels: list[str]) -> int:
        number = max([*self.issues, 0]) + 1
        author = self.issues[parent].author if parent in self.issues else Actor("Hawkynt")
        self.issues[number] = Issue(
            number, title, body, "open", author, frozenset(labels), False, NOW, NOW
        )
        self.children.setdefault(parent, []).append(number)
        return number

    def sub_issues(self, parent: int) -> list[Issue]:
        return [self.issue(number) for number in self.children.get(parent, [])]


@dataclass
class FakeWorkspace:
    root: Path
    trees: dict[int, list[CommitInfo]] = field(default_factory=dict)
    remote: dict[str, str] = field(default_factory=dict)
    pushes: list[tuple[str, str | None]] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    local: dict[int, str] = field(default_factory=dict)
    """Worktrees on this machine: issue to branch."""
    rebase_ok: bool = True
    shas: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    head_listener: Callable[[str, str], None] | None = None

    def worktree(self, repository: str, issue: int, branch: str, base: str) -> Path:
        path = self.root / str(issue)
        path.mkdir(parents=True, exist_ok=True)
        self.trees.setdefault(issue, [])
        return path

    def add_commit(self, issue: int, message: str) -> None:
        self.trees[issue].append(CommitInfo(f"c{next(self.shas)}", message))

    def commits(self, worktree: Path, base: str) -> list[CommitInfo]:
        return list(self.trees[int(worktree.name)])

    def head(self, worktree: Path) -> str:
        commits = self.trees[int(worktree.name)]
        return commits[-1].sha if commits else "base"

    def rebase(self, worktree: Path, base: str) -> bool:
        return self.rebase_ok

    def push(self, repository: str, worktree: Path, branch: str, expected: str | None) -> str:
        if self.remote.get(branch) != expected:
            raise PushRejected(branch)
        head = self.head(worktree)
        self.remote[branch] = head
        self.pushes.append((branch, expected))
        if self.head_listener:
            self.head_listener(branch, head)
        return head

    def remote_head(self, repository: str, branch: str) -> str | None:
        return self.remote.get(branch)

    def remove(self, repository: str, issue: int, branch: str) -> None:
        self.removed.append(issue)
        self.local.pop(issue, None)

    def worktrees(self, repository: str) -> dict[int, str]:
        return dict(self.local)


@dataclass
class ScriptedAgent:
    """Each call runs the next step: it may change the workspace, and returns the report."""

    steps: list[Callable[[AgentRun], RunReport]]
    runs: list[AgentRun] = field(default_factory=list)

    def __call__(self, run: AgentRun) -> RunReport:
        self.runs.append(run)
        return self.steps.pop(0)(run)
