from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.application.issue_run import IssueRun, RunSettings, Verdict
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.outcome import Outcome, Question
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentRun, RunReport, UsageLimit
from github_issue_worker.ports.repository import Check
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

    def verify(self, worktree: Path) -> tuple[bool, str]:
        return (
            self.verify_results.pop(0) if len(self.verify_results) > 1 else self.verify_results[0]
        )

    def run(
        self, kind: RunKind = RunKind.FIRST, comments: tuple[Comment, ...] = (), **settings: object
    ) -> Verdict:
        return IssueRun(
            host=self.host,
            workspace=self.workspace,
            coordinator=self.coordinator,
            agent=self.agent,
            verify=self.verify,
            sessions=self.sessions,
            clock=self.clock,
            sleep=lambda seconds: self.clock.advance(timedelta(seconds=seconds)),
            settings=RunSettings(**settings),  # type: ignore[arg-type]
        ).run(self.handle, ISSUE, comments, kind)


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
    assert any("merged" in body.lower() for _, body in h.host.comments)
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


def test_a_question_parks_the_issue_and_keeps_the_worktree(tmp_path: Path) -> None:
    question = Question("Retry on 5xx too?", ("yes", "no"), "yes")
    outcome = Outcome("needs-input", "Two readings.", questions=(question,))
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok(outcome), commit=None)])
    comment = Comment(77, 42, OWNER, "please", T0, T0)
    verdict = h.run(comments=(comment,))

    assert verdict.kind == "needs-input"
    ((_, body),) = h.host.comments
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
    assert "no outcome" in h.host.comments[-1][1]
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
