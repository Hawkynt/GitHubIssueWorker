"""F16.5 to F16.9 — working an open pull request on its own branch, then merging it."""

from __future__ import annotations

from pathlib import Path

from github_issue_worker.domain.issue import Issue
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import RunReport
from github_issue_worker.ports.repository import Check, PullRequest, ReviewState, ReviewThread
from github_issue_worker.ports.workspace import CommitInfo
from tests.application.test_issue_run import GOOD_COMMIT, OWNER, T0, Harness, done, step

BRANCH = "issue/42-retry-on-timeout"
"""The harness claims on this branch; here it is the pull request's own head branch."""
AUTHOR_COMMITS = (CommitInfo("author-1", "wip"), CommitInfo("author-2", "more wip"))
"""The contributor's commits: they break the house rules, and are never the worker's to touch."""


def pull_harness(tmp_path: Path, steps: list, checks: list[list[Check]]) -> Harness:  # type: ignore[type-arg]
    h = Harness(tmp_path, steps)
    assert h.handle.claim.branch == BRANCH  # the manager claims a pull request on its head branch
    h.issue = Issue(42, "Retry uploads", "Fixes #3.", "open", OWNER, frozenset(), True, T0, T0)
    h.host.issues[42] = h.issue
    h.host.pulls[42] = PullRequest(
        42,
        "author-2",
        "https://github.com/Hawkynt/ShiftDiff/pull/42",
        "clean",
        title="Retry uploads",
        head_ref=BRANCH,
        head_repository="Hawkynt/ShiftDiff",
        base_ref="main",
        body="Fixes #3.",
    )
    h.host.pull_bodies[42] = (BRANCH, "Retry uploads")
    h.host.head_of[BRANCH] = "author-2"
    h.workspace.trees[42] = list(AUTHOR_COMMITS)
    h.workspace.remote[BRANCH] = "author-2"
    h.host.check_results = checks
    return h


RED = [Check("tests", "completed", "failure", "3 failed in test_upload.py")]
GREEN = [Check("tests", "completed", "success")]


def test_given_red_checks_when_worked_then_the_fix_is_pushed_on_top_and_the_pr_merged(
    tmp_path: Path,
) -> None:
    h = pull_harness(
        tmp_path,
        lambda h: [step(h.workspace, RunReport("s1", done()))],
        checks=[RED, GREEN],
    )
    verdict = h.run(RunKind.PULL_REQUEST)

    assert verdict.kind == "merged"
    assert "3 failed in test_upload.py" in h.agent.runs[0].prompt
    assert "never rebase" in h.agent.runs[0].prompt
    assert h.workspace.ff_pushes == [BRANCH] and h.workspace.pushes == []
    assert h.host.merged == [(42, "squash", h.workspace.remote[BRANCH])]
    assert h.host.closed == [] and h.host.deleted_branches == []
    assert [commit.sha for commit in h.workspace.trees[42][:2]] == ["author-1", "author-2"]


def test_given_a_green_pull_request_without_concerns_when_worked_then_it_merges_at_once(
    tmp_path: Path,
) -> None:
    h = pull_harness(tmp_path, [], checks=[GREEN])
    verdict = h.run(RunKind.PULL_REQUEST)
    assert verdict.kind == "merged"
    assert h.agent.runs == [] and h.workspace.ff_pushes == []
    assert h.host.merged == [(42, "squash", "author-2")]


def test_given_review_threads_when_worked_then_each_is_in_the_brief(tmp_path: Path) -> None:
    h = pull_harness(
        tmp_path,
        lambda h: [step(h.workspace, RunReport("s1", done()))],
        checks=[GREEN],
    )
    thread = ReviewThread("T1", "src/upload.py", 42, "reviewer", "Retry only on 5xx.")
    h.host.reviews[42] = ReviewState("CHANGES_REQUESTED", (thread,))
    assert h.run(RunKind.PULL_REQUEST).kind == "merged"
    prompt = h.agent.runs[0].prompt
    assert "src/upload.py:42 by reviewer: Retry only on 5xx." in prompt
    assert "A review requested changes" in prompt


def test_given_the_base_moved_when_worked_then_it_is_merged_in_never_rebased(
    tmp_path: Path,
) -> None:
    h = pull_harness(tmp_path, [], checks=[GREEN])
    h.host.states = ["behind", "behind", "clean"]  # the run start reads it once more
    h.workspace.rebase_ok = True
    assert h.run(RunKind.PULL_REQUEST).kind == "merged"
    assert h.workspace.merged_in == ["main"] and h.workspace.ff_pushes == [BRANCH]
    assert h.workspace.trees[42][-1].message.startswith("* main merged into the branch")


def test_given_the_authors_commits_break_the_rules_when_checked_then_only_new_ones_count(
    tmp_path: Path,
) -> None:
    h = pull_harness(
        tmp_path,
        lambda h: [step(h.workspace, RunReport("s1", done()), commit=GOOD_COMMIT)],
        checks=[RED, GREEN],
    )
    assert h.run(RunKind.PULL_REQUEST).kind == "merged"
    assert len(h.agent.runs) == 1  # no rewording was asked for the author's "wip" commits


def test_given_a_question_when_worked_then_the_pull_request_waits_for_an_answer(
    tmp_path: Path,
) -> None:
    from github_issue_worker.domain.outcome import Outcome, Question

    asks = Outcome(
        "needs-input", "Which retry limit?", questions=(Question("Limit?", ("3", "5"), "3"),)
    )
    h = pull_harness(
        tmp_path,
        lambda h: [step(h.workspace, RunReport("s1", asks), commit=None)],
        checks=[RED],
    )
    assert h.run(RunKind.PULL_REQUEST).kind == "needs-input"
    assert h.host.worker_label[42] == "worker:needs-input"
    assert h.host.merged == []
