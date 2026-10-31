from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.application.issue_run import IssueRun, RunSettings, Verdict
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.outcome import Outcome, Question
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentRun, RunReport, UsageLimit
from github_issue_worker.ports.repository import Check, WaitingRun
from tests.application.run_fakes import FakeHost, FakeWorkspace, ScriptedAgent
from tests.fakes import FakeClock, InMemoryClaimStore

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
POLICY = ClaimPolicy(timedelta(days=7), timedelta(days=1))
WORKER = Holder("office-pc", "codex")
OWNER = Actor("Hawkynt")
ISSUE = Issue(
    42,
    "Retry on timeout",
    "It gives up after one timeout.",
    "open",
    OWNER,
    frozenset(),
    False,
    T0,
    T0,
)
GOOD_COMMIT = "# the client retried only once\n\nIt now honours the configured retry count."


def done(summary: str = "Retries now honour the count.") -> Outcome:
    return Outcome(
        "done",
        summary,
        scenarios=("Given a timeout When retrying Then 3 attempts",),
        labels=("bug",),
    )


def step(workspace: FakeWorkspace, report: RunReport, commit: str | None = GOOD_COMMIT):  # type: ignore[no-untyped-def]
    def act(run: AgentRun) -> RunReport:
        if commit:
            workspace.add_commit(42, commit)
        return report

    return act


class Harness:
    def __init__(
        self, tmp_path: Path, steps: list, verify: list[tuple[bool, str]] | None = None
    ) -> None:  # type: ignore[type-arg]
        self.clock = FakeClock(T0)
        self.store = InMemoryClaimStore()
        self.coordinator = Coordinator(self.store, self.clock, POLICY)
        self.host = FakeHost(issues={42: ISSUE})
        self.workspace = FakeWorkspace(tmp_path)
        self.workspace.head_listener = lambda branch, sha: self.host.head_of.__setitem__(
            branch, sha
        )
        self.agent = ScriptedAgent(steps(self) if callable(steps) else steps)
        self.verify_results = list(verify or [(True, "all green")])
        self.sessions: dict[tuple[int, str], str] = {}
        handle = self.coordinator.claim(42, WORKER, branch="issue/42-retry-on-timeout")
        assert handle is not None
        self.handle = handle
        self.issue = ISSUE

    def verify(self, worktree: Path) -> tuple[bool, str]:
        return (
            self.verify_results.pop(0) if len(self.verify_results) > 1 else self.verify_results[0]
        )

    def run(
        self, kind: RunKind = RunKind.FIRST, comments: tuple[Comment, ...] = (), **settings: object
    ) -> Verdict:
        blocked = settings.pop("blocked", lambda blocker: None)
        return IssueRun(
            host=self.host,
            workspace=self.workspace,
            coordinator=self.coordinator,
            agent=self.agent,
            verify=self.verify,
            sessions=self.sessions,
            clock=self.clock,
            sleep=lambda seconds: self.clock.advance(timedelta(seconds=seconds)),
            # The understanding run has its own tests; here it is off unless a test asks for it.
            settings=RunSettings(**{"understand_first": False, **settings}),  # type: ignore[arg-type]
            blocked=blocked,  # type: ignore[arg-type]
        ).run(self.handle, self.issue, comments, kind)


def ok(outcome: Outcome | None = None, session: str = "s1") -> RunReport:
    return RunReport(session=session, outcome=outcome or done())


def test_the_happy_path_ends_merged_closed_and_clean(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    verdict = h.run()

    assert verdict.kind == "merged"
    ((number, (branch, text)),) = h.host.pull_bodies.items()
    assert branch == "issue/42-retry-on-timeout"
    assert "Closes #42" in text and "Given a timeout" in text
    assert h.host.merged == [(number, "squash", "c1")]
    assert h.host.deleted_branches == ["issue/42-retry-on-timeout"]
    assert h.host.closed == [42]
    assert any("merged" in body.lower() for _, body in h.host.posted)
    assert h.host.worker_label[42] is None
    current = h.store.current(42)
    assert current is not None and current.state is ClaimState.DONE
    assert h.workspace.removed == [42]
    assert h.sessions[(42, "codex")] == "s1"


def test_the_first_run_labels_an_unlabelled_issue_from_the_existing_labels(tmp_path: Path) -> None:
    outcome = Outcome("done", "x", labels=("bug", "invented"))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome))])
    h.run()
    assert h.host.added_labels[42] == ["bug"]


def test_a_missing_label_is_never_created_when_create_missing_labels_is_off(
    tmp_path: Path,
) -> None:
    outcome = Outcome("done", "x", labels=("bug", "invented"))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome))])
    h.run()
    assert h.host.added_labels[42] == ["bug"]
    assert h.host.created_labels == []


def test_a_missing_label_is_created_and_applied_when_create_missing_labels_is_on(
    tmp_path: Path,
) -> None:
    outcome = Outcome("done", "x", labels=("bug", "invented"))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome))])
    h.run(create_missing_labels=True)
    assert h.host.created_labels == ["invented"]
    assert h.host.added_labels[42] == ["bug", "invented"]


def test_an_existing_label_is_applied_without_being_created_when_create_missing_labels_is_on(
    tmp_path: Path,
) -> None:
    outcome = Outcome("done", "x", labels=("bug",))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome))])
    h.run(create_missing_labels=True)
    assert h.host.created_labels == []
    assert h.host.added_labels[42] == ["bug"]


def test_a_label_created_by_an_earlier_run_is_applied_again_without_being_created_twice(
    tmp_path: Path,
) -> None:
    outcome = Outcome("done", "x", labels=("invented",))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome))])
    h.host.repo_labels.add("invented")  # an earlier run already created it on the repository
    h.run(create_missing_labels=True)
    assert h.host.created_labels == []
    assert h.host.added_labels[42] == ["invented"]


def test_the_understanding_run_creates_a_missing_label_when_create_missing_labels_is_on(
    tmp_path: Path,
) -> None:
    def steps(h: Harness) -> list:  # type: ignore[type-arg]
        understand = lambda run: RunReport(  # noqa: E731
            session="s1",
            outcome=Outcome(
                "done", "Understood.", restatement="Goal: retry.", labels=("bug", "invented")
            ),
        )
        return [understand, step(h.workspace, ok())]

    h = Harness(tmp_path, steps)
    h.run(understand_first=True, create_missing_labels=True)
    assert h.host.created_labels == ["invented"]
    assert h.host.added_labels[42] == ["bug", "invented"]


def test_a_question_parks_the_issue_and_keeps_the_worktree(tmp_path: Path) -> None:
    question = Question("Retry on 5xx too?", ("yes", "no"), "yes")
    outcome = Outcome("needs-input", "Two readings.", questions=(question,))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome), commit=None)])
    comment = Comment(77, 42, OWNER, "please", T0, T0)
    verdict = h.run(comments=(comment,))

    assert verdict.kind == "needs-input"
    ((_, body),) = h.host.posted
    assert "Retry on 5xx too?" in body and "yes" in body and "Recommendation" in body
    assert h.host.worker_label[42] == "worker:needs-input"
    current = h.store.current(42)
    assert current is not None and current.state is ClaimState.RELEASED
    assert current.step == "needs-input" and current.consumed_through.comment == 77
    assert h.workspace.removed == []


def test_a_usage_limit_pauses_the_claim_until_the_reset(tmp_path: Path) -> None:
    reset = T0 + timedelta(hours=4)
    limited = RunReport(session="s1", outcome=None, limit=UsageLimit("limit", reset))
    h = Harness(tmp_path, lambda h: [step(h.workspace, limited, commit=None)])
    verdict = h.run()

    assert verdict.kind == "paused" and verdict.until == reset
    current = h.store.current(42)
    assert current is not None and current.state is ClaimState.PAUSED
    assert current.paused_until == reset
    assert h.host.worker_label[42] == "worker:paused"


def test_a_limit_without_a_reset_pauses_for_the_probe_interval(tmp_path: Path) -> None:
    limited = RunReport(session="s1", outcome=None, limit=UsageLimit("limit", None))
    h = Harness(tmp_path, lambda h: [step(h.workspace, limited, commit=None)])
    assert h.run().until == T0 + timedelta(minutes=5)


def test_failed_verification_goes_back_to_the_agent_once(tmp_path: Path) -> None:
    h = Harness(
        tmp_path,
        lambda h: [step(h.workspace, ok()), step(h.workspace, ok(), commit=None)],
        verify=[(False, "FAILED test_retry"), (True, "green")],
    )
    assert h.run().kind == "merged"
    assert "FAILED test_retry" in h.agent.runs[1].prompt
    assert h.agent.runs[1].session == "s1"


def test_commits_that_break_the_house_rules_go_back_for_rewording(tmp_path: Path) -> None:
    h = Harness(
        tmp_path,
        lambda h: [
            step(h.workspace, ok(), commit="fixed the retry"),
            step(h.workspace, ok(), commit=None),
        ],
    )

    def reword(run: AgentRun) -> RunReport:
        h.workspace.trees[42] = []
        h.workspace.add_commit(42, GOOD_COMMIT)
        return ok()

    h.agent.steps[1] = reword
    assert h.run().kind == "merged"
    assert "prefix" in h.agent.runs[1].prompt


def test_no_commits_is_not_done(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(), commit=None), step(h.workspace, ok())])
    assert h.run().kind == "merged"
    assert "no commit" in h.agent.runs[1].prompt.lower()


def test_red_ci_is_repaired_by_the_agent(tmp_path: Path) -> None:
    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        h.host.states = ["blocked", "clean"]
        h.host.check_results = [[Check("CI", "completed", "failure", "test_retry failed")], []]
        return [
            step(h.workspace, ok()),
            step(h.workspace, ok(), commit="# retry the second time\n\nBody."),
        ]

    h = Harness(tmp_path, setup)
    assert h.run().kind == "merged"
    assert "test_retry failed" in h.agent.runs[1].prompt
    assert len(h.workspace.pushes) == 2


def test_a_merge_blocked_without_failing_checks_waits_then_releases(tmp_path: Path) -> None:
    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        h.host.states = ["blocked"]
        h.host.check_results = [[]]
        return [step(h.workspace, ok())]

    h = Harness(tmp_path, setup)
    verdict = h.run(checks_timeout=timedelta(minutes=30))
    assert verdict.kind == "awaiting-review"
    current = h.store.current(42)
    assert current is not None and current.state is ClaimState.RELEASED
    assert current.step == "awaiting-review"
    assert h.host.worker_label[42] == "worker:awaiting-review"
    assert h.host.merged == []


def test_a_conflict_is_rebased(tmp_path: Path) -> None:
    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        h.host.states = ["dirty", "clean"]
        return [step(h.workspace, ok())]

    h = Harness(tmp_path, setup)
    assert h.run().kind == "merged"
    assert len(h.workspace.pushes) == 2


def test_agent_errors_twice_fail_the_issue_with_a_comment(tmp_path: Path) -> None:
    broken = RunReport(session=None, outcome=None, error="the run wrote no outcome")
    h = Harness(
        tmp_path, lambda h: [step(h.workspace, broken, None), step(h.workspace, broken, None)]
    )
    verdict = h.run()
    assert verdict.kind == "failed"
    assert h.host.worker_label[42] == "worker:failed"
    assert "no outcome" in h.host.posted[-1][1]
    current = h.store.current(42)
    assert current is not None and current.step == "failed" and current.state is ClaimState.RELEASED


def test_a_lost_claim_stops_before_any_push(tmp_path: Path) -> None:
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
    assert h.workspace.pushes == []
    assert h.host.pulls == {}


def test_ci_that_never_started_goes_to_the_maintainer_not_the_agent(tmp_path: Path) -> None:
    # Seen live: with Actions billing blocked, every job "fails" without starting. Nothing in the
    # code can fix that, so the agent must not be asked to.
    reason = (
        "The job was not started because recent account payments have failed or your spending "
        "limit needs to be increased."
    )

    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        h.host.states = ["blocked"]
        h.host.check_results = [[Check("CI", "completed", "failure", "", (reason,))]]
        return [step(h.workspace, ok())]

    h = Harness(tmp_path, setup)
    verdict = h.run()
    assert verdict.kind == "awaiting-review"
    assert len(h.agent.runs) == 1
    assert reason in h.host.posted[-1][1]
    assert h.host.worker_label[42] == "worker:awaiting-review"


def test_the_run_reports_each_step_it_enters(tmp_path: Path) -> None:
    steps: list[str] = []
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    verdict = IssueRun(
        host=h.host,
        workspace=h.workspace,
        coordinator=h.coordinator,
        agent=h.agent,
        verify=h.verify,
        sessions=h.sessions,
        clock=h.clock,
        sleep=lambda seconds: None,
        settings=RunSettings(understand_first=False),
        progress=steps.append,
    ).run(h.handle, ISSUE, (), RunKind.FIRST)
    assert verdict.kind == "merged"
    assert steps == ["implement", "verify", "push", "checks", "merge"]


def test_a_run_stopped_by_the_maintainer_releases_the_issue(tmp_path: Path) -> None:
    from github_issue_worker.application.issue_run import RunStopped

    def stopped(run: AgentRun) -> RunReport:
        raise RunStopped("stopped from the UI")

    h = Harness(tmp_path, [stopped])
    verdict = h.run()
    assert verdict.kind == "stopped"
    current = h.store.current(42)
    assert (
        current is not None and current.state is ClaimState.RELEASED and current.step == "stopped"
    )
    assert h.host.worker_label[42] == "worker:queued"
    assert h.workspace.pushes == [] and len(h.agent.runs) == 1


def test_the_pr_the_squash_commit_and_the_comment_are_change_lines_not_prose(
    tmp_path: Path,
) -> None:
    commit = (
        "+ retries honour the configured count\n\n"
        "+ each timeout waits 1, 2 and 4 seconds\n"
        "* the client logs every retry"
    )
    summary = Outcome("done", "A long paragraph of prose about what was done and why.")
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(summary), commit=commit)])
    assert h.run().kind == "merged"

    lines = [
        "+ retries honour the configured count",
        "+ each timeout waits 1, 2 and 4 seconds",
        "* the client logs every retry",
    ]
    ((_, body),) = h.host.pull_bodies.values()
    assert all(line in body for line in lines)
    assert "A long paragraph of prose" not in body
    assert "Closes #42" in body
    ((title, message),) = h.host.merge_messages
    assert title.startswith("+ Retry on timeout") and title.endswith("(#100)")
    assert message.splitlines() == lines
    merged_comment = h.host.posted[-1][1]
    assert (
        all(line in merged_comment for line in lines) and "paragraph of prose" not in merged_comment
    )


def test_a_run_that_finds_its_pull_request_merges_under_that_title(tmp_path: Path) -> None:
    # A continuation after a restart did not open the PR itself; its squash subject is still the
    # PR's change line, not the bare issue title.
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.head_of["issue/42-retry-on-timeout"] = "old"
    h.host.create_pull_request("issue/42-retry-on-timeout", "main", "+ Retry on timeout", "")
    assert h.run().kind == "merged"
    ((title, _),) = h.host.merge_messages
    assert title == "+ Retry on timeout (#100)"


def test_merge_conflict_stays_checkpointed_during_repair_and_resource_pause(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    h.host.states = ["dirty"]
    h.workspace.rebase_ok = False

    def repair(run: AgentRun) -> RunReport:
        current = h.store.current(42)
        assert current is not None and current.merge_conflict
        return RunReport(session="s", outcome=None, limit=UsageLimit("Rate control", T0))

    h.agent.steps = [step(h.workspace, ok()), repair]
    assert h.run().kind == "paused"
    checkpoint = h.store.current(42)
    assert checkpoint is not None and checkpoint.merge_conflict
    assert checkpoint.wait_reason == "Rate control"


def test_resolved_merge_conflict_clears_checkpoint_after_restart(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.handle = h.coordinator.set_merge_conflict(h.handle, True)
    # A new IssueRun reads the checkpoint, observes clean mergeability and clears it.
    assert h.run().kind == "merged"
    checkpoint = h.store.current(42)
    assert checkpoint is not None and not checkpoint.merge_conflict


def test_failed_rebase_marks_conflict_even_when_remote_only_reports_behind(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    h.host.states = ["behind"]
    h.workspace.rebase_ok = False

    def repair(run: AgentRun) -> RunReport:
        current = h.store.current(42)
        assert current is not None and current.merge_conflict
        return ok(Outcome("needs-input", "Please resolve the conflicting changes."))

    h.agent.steps = [step(h.workspace, ok()), repair]
    assert h.run().kind == "needs-input"
    current = h.store.current(42)
    assert current is not None and current.merge_conflict


def test_conflict_detected_at_merge_is_checkpointed_before_the_merge_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.states = ["clean", "dirty"]

    def reject(*args: object, **kwargs: object) -> str:
        raise RuntimeError("merge conflict")

    monkeypatch.setattr(h.host, "merge", reject)
    with pytest.raises(RuntimeError, match="merge conflict"):
        h.run()
    checkpoint = h.store.current(42)
    assert checkpoint is not None and checkpoint.merge_conflict


def test_ci_waiting_for_approval_is_reported_as_a_blocker_until_it_runs(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.states = ["blocked", "blocked", "clean"]
    h.host.approvals = [[WaitingRun(7, "CI", "https://x/runs/7", "pull_request")], []]
    seen: list[str | None] = []
    run = h.run(blocked=lambda blocker: seen.append(blocker.reason if blocker else None))
    assert run.kind == "merged"
    assert seen[0] == (
        "CI's pull request run waits for approval to run; the merge needs it, whatever else "
        "already runs"
    )
    assert seen[-1] is None  # cleared once the run started


def test_while_ci_waits_for_approval_the_run_looks_again_within_ten_seconds(
    tmp_path: Path,
) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.states = ["blocked", "blocked", "clean"]
    h.host.approvals = [[WaitingRun(7, "CI", "https://x/runs/7", "pull_request")], []]
    slept: list[float] = []
    original = h.clock.advance
    h.clock.advance = lambda delta: (slept.append(delta.total_seconds()), original(delta))[1]  # type: ignore[method-assign]
    assert h.run().kind == "merged"
    assert slept[0] == 10.0  # blocked: soon again
    assert slept[1] == 30.0  # no longer blocked: the normal interval


def test_ci_of_the_repositorys_own_branch_is_approved_without_asking(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.states = ["blocked", "clean"]
    h.host.approvals = [[WaitingRun(9, "CI", "https://x/runs/9", "pull_request", own=True)], []]
    seen: list[object] = []
    assert h.run(blocked=seen.append).kind == "merged"
    assert h.host.approved == [9]
    assert all(blocker is None for blocker in seen)  # nobody was asked


def test_worker_skip_added_during_an_agent_run_stops_at_the_next_checkpoint_and_releases_the_claim(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    def skip_during_implement(h: Harness) -> list:  # type: ignore[type-arg]
        def act(run: AgentRun) -> RunReport:
            h.workspace.add_commit(42, GOOD_COMMIT)
            h.host.issues[42] = replace(ISSUE, labels=frozenset({"worker:working", "worker:skip"}))
            return ok()

        return [act]

    h = Harness(tmp_path, skip_during_implement)
    verdict = h.run()

    assert verdict.kind == "skipped"
    current = h.store.current(42)
    assert (
        current is not None and current.state is ClaimState.RELEASED and current.step == "skipped"
    )
    assert h.host.worker_label[42] is None
    assert h.workspace.pushes == []
    assert h.host.pulls == {}
    assert h.host.posted == []


def test_worker_skip_added_during_understanding_stops_before_posting_restatement_or_implementing(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    def skip_during_understand(h: Harness) -> list:  # type: ignore[type-arg]
        def understand(run: AgentRun) -> RunReport:
            h.host.issues[42] = replace(ISSUE, labels=frozenset({"worker:working", "worker:skip"}))
            return ok(Outcome("done", "Understood.", restatement="Goal: retry.", labels=("bug",)))

        return [understand, step(h.workspace, ok())]

    h = Harness(tmp_path, skip_during_understand)
    verdict = h.run(understand_first=True)

    assert verdict.kind == "skipped"
    assert len(h.agent.runs) == 1
    assert h.host.posted == []
    assert h.host.added_labels == {}
    assert h.host.worker_label[42] is None
    current = h.store.current(42)
    assert (
        current is not None and current.state is ClaimState.RELEASED and current.step == "skipped"
    )


def test_worker_skip_added_while_waiting_for_checks_stops_at_the_next_poll_and_releases_the_claim(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.host.states = ["blocked", "clean"]
    h.host.check_results = [[]]

    original_sleep = h.clock.advance

    def skip_while_waiting(delta: timedelta) -> None:
        h.host.issues[42] = replace(ISSUE, labels=frozenset({"worker:working", "worker:skip"}))
        original_sleep(delta)

    h.clock.advance = skip_while_waiting  # type: ignore[method-assign]
    verdict = h.run()

    assert verdict.kind == "skipped"
    assert h.host.merged == []
    assert h.host.worker_label[42] is None
    current = h.store.current(42)
    assert (
        current is not None and current.state is ClaimState.RELEASED and current.step == "skipped"
    )


def test_worker_skip_added_before_an_outward_write_stops_at_the_fence_and_releases_the_claim(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    h.issue = replace(ISSUE, labels=frozenset({"worker:skip"}))
    verdict = h.run()

    assert verdict.kind == "skipped"
    assert h.agent.runs == []
    assert h.host.worker_label[42] is None
    current = h.store.current(42)
    assert (
        current is not None and current.state is ClaimState.RELEASED and current.step == "skipped"
    )
