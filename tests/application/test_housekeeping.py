"""Branches and worktrees of finished issues go, remotely and locally (F5.5)."""

from __future__ import annotations

from pathlib import Path

from github_issue_worker.application.housekeeping import Housekeeping
from github_issue_worker.domain.issue import Actor, Issue
from github_issue_worker.ports.repository import PullRequest
from tests.application.run_fakes import NOW, FakeHost, FakeWorkspace

OWNER = Actor("Hawkynt")


def issue(number: int, closed: bool = False) -> Issue:
    return Issue(
        number,
        f"Issue {number}",
        "",
        "closed" if closed else "open",
        OWNER,
        frozenset(),
        False,
        NOW,
        NOW,
    )


def pull(number: int, state: str, merged: bool = False) -> PullRequest:
    return PullRequest(number, "sha", f"https://x/pull/{number}", "clean", merged, state=state)


def setup(tmp_path: Path) -> tuple[FakeHost, FakeWorkspace, Housekeeping]:
    host = FakeHost(issues={n: issue(n, closed=n in (1, 2)) for n in range(1, 7)})
    workspace = FakeWorkspace(tmp_path)
    for number in range(1, 7):
        workspace.local[number] = f"issue/{number}-x"
        host.remote_branches.append(f"issue/{number}-x")
    host.branch_pulls = {
        "issue/1-x": [pull(10, "closed", merged=True)],  # merged and closed: goes
        "issue/2-x": [],  # issue closed without a pull request: goes
        "issue/3-x": [pull(30, "closed")],  # pull request closed unmerged, issue open: goes
        "issue/4-x": [pull(40, "open")],  # still in review: stays
        "issue/5-x": [],  # pushed, pull request not opened yet: stays
        "issue/6-x": [pull(60, "closed", merged=True)],  # merged, but worked here again: stays
    }
    return host, workspace, Housekeeping(host, workspace)


def test_finished_branches_are_deleted_remotely(tmp_path: Path) -> None:
    host, _, housekeeping = setup(tmp_path)
    housekeeping.sweep(mine={6}, claimed={6})
    assert sorted(host.deleted_branches) == ["issue/1-x", "issue/2-x", "issue/3-x"]


def test_finished_worktrees_are_removed_locally(tmp_path: Path) -> None:
    _, workspace, housekeeping = setup(tmp_path)
    housekeeping.sweep(mine={6}, claimed={5, 6})
    # closed issues go; 5 is now another manager's; 3 and 4 are open and may come back here
    assert sorted(workspace.removed) == [1, 2, 5]


def test_a_branch_someone_holds_a_claim_on_is_never_touched(tmp_path: Path) -> None:
    host, workspace, housekeeping = setup(tmp_path)
    housekeeping.sweep(mine=set(), claimed={1, 2, 3})
    assert host.deleted_branches == ["issue/6-x"]  # the only finished one nobody holds
    # held elsewhere, this machine's copies are stale; the claims keep the branches on GitHub
    assert sorted(workspace.removed) == [1, 2, 3]


def test_only_issue_branches_are_considered(tmp_path: Path) -> None:
    host, _, housekeeping = setup(tmp_path)
    host.remote_branches.append("feature/manual")
    host.branch_pulls["feature/manual"] = [pull(99, "closed", merged=True)]
    housekeeping.sweep(mine=set(), claimed=set())
    assert "feature/manual" not in host.deleted_branches


def test_agents_are_told_to_keep_to_their_branch_because_it_is_deleted() -> None:
    from github_issue_worker.application.prompt import CONTRACT

    assert "Create no other branch" in CONTRACT
    assert "deletes the branch on\n  GitHub and here, together with this worktree" in CONTRACT
