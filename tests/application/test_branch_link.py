"""F5.3: the issue branch is linked to its issue, so it shows in the Development panel."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from github_issue_worker.application.coordinator import ClaimHandle
from github_issue_worker.domain.claim import Holder
from github_issue_worker.domain.issue import Actor, Comment
from github_issue_worker.ports.agents import AgentRun, RunReport
from github_issue_worker.ports.github import GitHubError
from tests.application.test_issue_run import GOOD_COMMIT, Harness, ok, step

BRANCH = "issue/42-retry-on-timeout"


def test_a_new_branch_is_created_linked_to_its_issue_before_the_first_push(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])

    assert h.run().kind == "merged"
    assert h.host.link_calls == [(42, BRANCH, "base")]
    assert h.host.links[42] == [BRANCH]
    # The push fast-forwards the branch the link created, under the lease.
    assert h.workspace.pushes == [(BRANCH, "base")]


def test_an_already_linked_branch_is_reused_without_a_second_link_or_a_reset(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.links[42] = [BRANCH]
    h.host.remote_branches.append(BRANCH)
    h.workspace.remote[BRANCH] = "c0"

    assert h.run().kind == "merged"
    assert h.host.link_calls == []
    assert h.workspace.pushes == [(BRANCH, "c0")]


def test_a_branch_linked_by_a_run_that_crashed_before_its_push_is_reused(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.links[42] = [BRANCH]
    h.workspace.remote[BRANCH] = "base"

    assert h.run().kind == "merged"
    assert h.host.link_calls == []
    assert h.workspace.pushes == [(BRANCH, "base")]


def test_an_existing_unlinked_branch_keeps_its_commits_and_linking_is_asked_for_once(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.workspace.remote[BRANCH] = "c0"

    assert h.run().kind == "merged"
    assert h.host.link_calls == []
    assert h.workspace.pushes == [(BRANCH, "c0")]
    asks = [body for _, body in h.host.posted if "link" in body and BRANCH in body]
    assert len(asks) == 1 and "Development" in asks[0]


def test_linking_is_not_asked_for_again_once_asked(tmp_path: Path) -> None:
    first = Harness(tmp_path / "a", lambda h: [step(h.workspace, ok())])
    first.workspace.remote[BRANCH] = "c0"
    first.run()
    (ask,) = [body for _, body in first.host.posted if "Development" in body]

    h = Harness(tmp_path / "b", lambda h: [step(h.workspace, ok())])
    h.workspace.remote[BRANCH] = "c0"
    at = datetime(2026, 10, 30, tzinfo=UTC)
    h.host.issue_comments.append(Comment(5, 42, Actor("Hawkynt"), ask, at, at))
    h.run()
    assert not [body for _, body in h.host.posted if "Development" in body]


def test_a_rejected_link_fails_the_run_before_anything_is_pushed(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.link_errors.append(GitHubError(200, "GraphQL: Name already exists on this repository"))

    verdict = h.run()

    assert verdict.kind == "failed"
    assert "Name already exists" in verdict.detail
    assert h.host.links == {} and h.workspace.pushes == [] and h.host.pulls == {}
    assert any("Name already exists" in body for _, body in h.host.posted)
    assert h.host.worker_label[42] == "worker:failed"


def test_a_later_run_retries_a_rejected_link(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok()), step(h.workspace, ok(), None)])
    h.host.link_errors.append(GitHubError(502, "Bad gateway"))
    with pytest.raises(GitHubError):  # GitHub's trouble, not the issue's: the run crashes
        h.run()
    assert h.host.links == {} and h.workspace.pushes == []

    stored = h.store.read(42)  # the claim was kept: the next tick continues it
    assert stored is not None
    h.handle = ClaimHandle(stored.version, stored.claim)
    assert h.run().kind == "merged"
    assert h.host.link_calls == [(42, BRANCH, "base"), (42, BRANCH, "base")]
    assert h.host.links[42] == [BRANCH]


def test_a_lost_claim_sends_no_link(tmp_path: Path) -> None:
    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        def steal(run: AgentRun) -> RunReport:
            h.workspace.add_commit(42, GOOD_COMMIT)
            stored = h.store.read(42)
            assert stored is not None
            h.store.replace(stored.version, stored.claim.replace(holder=Holder("laptop", "claude")))
            return ok()

        return [steal]

    h = Harness(tmp_path, setup)
    assert h.run().kind == "lost"
    assert h.host.link_calls == []
