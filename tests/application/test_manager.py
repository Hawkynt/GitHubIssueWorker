from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from github_issue_worker.application.coordinator import ClaimHandle, Coordinator
from github_issue_worker.application.issue_run import Verdict
from github_issue_worker.application.manager import Manager, RepositoryWatch, WorkerSlot
from github_issue_worker.application.poller import PollResult, PollState
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.work import RunKind
from tests.application.run_fakes import FakeHost
from tests.fakes import FakeClock, InMemoryClaimStore

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
POLICY = ClaimPolicy(timedelta(days=7), timedelta(days=1))
OWNER = Actor("Hawkynt")
ME = "office-pc-7f3a"


def issue(number: int, labels: frozenset[str] = frozenset(), minutes: int = 0) -> Issue:
    at = T0 + timedelta(minutes=minutes)
    return Issue(number, f"Issue {number}", "", "open", OWNER, labels, False, at, at)


@dataclass
class Limits:
    until: dict[str, datetime] = field(default_factory=dict)

    def limited_until(self, account: str) -> datetime | None:
        return self.until.get(account)

    def set_limited(self, account: str, until: datetime | None) -> None:
        if until is None:
            self.until.pop(account, None)
        else:
            self.until[account] = until


@dataclass
class Recorded:
    worker: str
    issue: int
    kind: RunKind


class Fixture:
    def __init__(self, workers: list[WorkerSlot], verdicts: list[Verdict] | None = None) -> None:
        self.clock = FakeClock(T0)
        self.store = InMemoryClaimStore()
        self.host = FakeHost()
        self.coordinator = Coordinator(self.store, self.clock, POLICY)
        self.limits = Limits()
        self.polls: list[PollResult] = []
        self.runs: list[Recorded] = []
        self.verdicts = list(verdicts or [])
        self.watch = RepositoryWatch(
            name="Hawkynt/ShiftDiff",
            host=self.host,
            coordinator=self.coordinator,
            store=self.store,
            mode="all",
            process_backlog=False,
        )
        self.manager = Manager(
            manager_id=ME,
            manager_tags=frozenset({"linux"}),
            repositories=[self.watch],
            workers=workers,
            poll=self.poll,
            poll_states={},
            limits=self.limits,
            clock=self.clock,
            trusted_users=(),
            run_issue=self.run_issue,
            spawn=lambda job: job(),
        )

    def poll(
        self, repository: str, state: PollState, backlog: bool
    ) -> tuple[PollResult, PollState]:
        result = self.polls.pop(0) if self.polls else PollResult()
        return result, replace(state, watched_since=state.watched_since or T0 - timedelta(hours=1))

    def run_issue(
        self,
        watch: RepositoryWatch,
        slot: WorkerSlot,
        handle: ClaimHandle,
        issue: Issue,
        comments: tuple[Comment, ...],
        kind: RunKind,
    ) -> Verdict:
        self.runs.append(Recorded(slot.name, issue.number, kind))
        verdict = self.verdicts.pop(0) if self.verdicts else Verdict("merged")
        if verdict.kind == "paused":
            self.coordinator.pause(handle, until=verdict.until or T0)
        elif verdict.kind == "merged":
            self.coordinator.complete(handle)
        else:
            self.coordinator.release(handle, step=verdict.kind)
        return verdict

    def new_issues(self, *issues: Issue) -> None:
        for item in issues:
            self.host.issues[item.number] = item
        self.polls.append(PollResult(issues=issues))


def slot(
    name: str, agent: str = "claude", account: str = "a", priority: int = 100, **kw: object
) -> WorkerSlot:
    return WorkerSlot(name=name, agent=agent, account=account, priority=priority, **kw)  # type: ignore[arg-type]


def test_a_new_issue_goes_to_the_preferred_idle_worker() -> None:
    f = Fixture([slot("codex", "codex", priority=2), slot("claude", priority=1)])
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.runs == [Recorded("claude", 1, RunKind.FIRST)]
    claim = f.store.current(1)
    assert claim is not None and claim.holder == Holder(ME, "claude")


def test_each_worker_holds_one_issue_and_the_rest_queue() -> None:
    f = Fixture([slot("claude")], verdicts=[Verdict("paused", until=T0 + timedelta(hours=1))])
    f.new_issues(issue(1), issue(2, minutes=1))
    f.manager.tick()
    assert [run.issue for run in f.runs] == [1]
    assert f.host.worker_label[2] == "worker:queued"
    assert f.store.current(2) is None


def test_queued_issues_are_picked_up_when_a_worker_frees_up() -> None:
    f = Fixture([slot("claude")], verdicts=[Verdict("needs-input"), Verdict("merged")])
    f.new_issues(issue(1), issue(2, minutes=1))
    f.manager.tick()
    f.manager.tick()
    assert [run.issue for run in f.runs] == [1, 2]


def test_routing_labels_choose_the_agent_kind() -> None:
    f = Fixture([slot("claude", priority=1), slot("codex", "codex", priority=9)])
    f.new_issues(issue(1, frozenset({"agent:codex"})))
    f.manager.tick()
    assert f.runs == [Recorded("codex", 1, RunKind.FIRST)]


def test_needs_tags_match_worker_or_manager_tags() -> None:
    f = Fixture([slot("claude", tags=frozenset({"gpu"}))])
    f.new_issues(
        issue(1, frozenset({"needs:gpu", "needs:linux"})), issue(2, frozenset({"needs:windows"}))
    )
    f.manager.tick()
    assert [run.issue for run in f.runs] == [1]
    assert f.host.worker_label[2] == "worker:unserved"


def test_a_limited_account_takes_no_work() -> None:
    f = Fixture(
        [slot("claude", account="anthropic", priority=1), slot("codex", "codex", "openai", 2)]
    )
    f.limits.until["anthropic"] = T0 + timedelta(hours=2)
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.runs == [Recorded("codex", 1, RunKind.FIRST)]


def test_a_paused_issue_resumes_by_itself_after_the_reset() -> None:
    reset = T0 + timedelta(hours=1)
    f = Fixture([slot("claude", account="anthropic")], verdicts=[Verdict("paused", until=reset)])
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.limits.until["anthropic"] == reset

    f.manager.tick()
    assert len(f.runs) == 1  # still before the reset

    f.clock.advance(timedelta(hours=1, minutes=1))
    f.manager.tick()
    assert f.runs[-1] == Recorded("claude", 1, RunKind.CONTINUATION)
    claim = f.store.current(1)
    assert claim is not None and claim.state is ClaimState.DONE


def test_own_claims_are_adopted_after_a_restart() -> None:
    f = Fixture([slot("claude")])
    f.host.issues[5] = issue(5)
    f.store.force_create(POLICY.new_claim(5, Holder(ME, "claude"), T0, "issue/5-x"))
    f.manager.tick()
    assert f.runs == [Recorded("claude", 5, RunKind.CONTINUATION)]


def test_another_managers_claim_is_left_alone() -> None:
    f = Fixture([slot("claude")])
    f.host.issues[5] = issue(5)
    f.store.force_create(POLICY.new_claim(5, Holder("laptop-01ab", "codex"), T0, "issue/5-x"))
    f.new_issues(issue(5))
    f.manager.tick()
    assert f.runs == []


def test_an_unavailable_worker_is_never_used() -> None:
    f = Fixture([slot("claude", available=False, reason="not logged in")])
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.runs == []
    assert f.host.worker_label[1] == "worker:unserved"
