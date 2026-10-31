from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.application.coordinator import ClaimHandle, Coordinator
from github_issue_worker.application.issue_run import Verdict
from github_issue_worker.application.manager import Manager, RepositoryWatch, WorkerSlot
from github_issue_worker.application.poller import PollResult, PollState
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.outcome import SubIssue
from github_issue_worker.domain.split import sub_issue_body
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.repository import Blocker
from github_issue_worker.product import MARKER_PREFIX
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


@pytest.mark.parametrize("is_bot", [False, True])
def test_untrusted_issue_is_logged_once_with_its_opener(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, is_bot: bool
) -> None:
    f = Fixture([slot("worker")])
    monkeypatch.setattr(f.host, "role", lambda login: "admin" if login == OWNER.login else "read")
    outsider_issue = replace(issue(1), author=Actor("outsider", is_bot=is_bot))
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            f.new_issues(outsider_issue)
            f.manager.tick()
    assert caplog.messages == [
        "Hawkynt/ShiftDiff#1: ignored issue opened by untrusted author outsider"
    ]
    assert all(record.levelno == logging.INFO for record in caplog.records)
    assert f.runs == []


@pytest.mark.parametrize("is_bot", [False, True])
def test_untrusted_comments_are_logged_once_per_comment_id(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    is_bot: bool,
) -> None:
    f = Fixture([slot("worker")], verdicts=[Verdict("needs-input")])
    monkeypatch.setattr(f.host, "role", lambda login: "admin" if login == OWNER.login else "read")
    f.new_issues(issue(1))
    f.manager.tick()
    f.host.issue_comments = [
        Comment(n, 1, Actor("outsider", is_bot=is_bot), "answer", T0, T0) for n in (10, 11)
    ]
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            f.new_issues(issue(1))
            f.manager.tick()
    assert caplog.messages == [
        f"Hawkynt/ShiftDiff#1: ignored comment {n} by untrusted author outsider" for n in (10, 11)
    ]
    assert all(record.levelno == logging.INFO for record in caplog.records)
    assert len(f.runs) == 1


@pytest.mark.parametrize("is_bot", [False, True])
def test_manager_marker_comments_are_not_logged_as_untrusted(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    is_bot: bool,
) -> None:
    f = Fixture([slot("worker")], verdicts=[Verdict("needs-input")])
    monkeypatch.setattr(f.host, "role", lambda login: "admin" if login == OWNER.login else "read")
    f.new_issues(issue(1))
    f.manager.tick()
    f.host.issue_comments = [
        Comment(10, 1, Actor("outsider", is_bot=is_bot), "<!-- ghiw:test -->", T0, T0)
    ]
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            f.new_issues(issue(1))
            f.manager.tick()
    assert caplog.messages == []
    assert len(f.runs) == 1


def test_a_paused_worker_claims_nothing() -> None:
    f = Fixture([slot("claude", enabled=False), slot("codex", "codex")])
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.runs == [Recorded("codex", 1, RunKind.FIRST)]


def test_a_running_worker_shows_its_issue_and_start_time() -> None:
    seen: list[tuple[object, ...]] = []

    def run_issue(watch, slot, handle, issue, comments, kind):  # type: ignore[no-untyped-def]
        seen.append((slot.current, slot.issue_title, slot.started_at, slot.branch))
        f.coordinator.complete(handle)
        return Verdict("merged")

    f = Fixture([slot("claude")])
    f.manager.run_issue = run_issue
    f.new_issues(issue(1))
    f.manager.tick()
    assert seen == [(("Hawkynt/ShiftDiff", 1), "Issue 1", T0, "issue/1-issue-1")]
    worker = f.manager.workers[0]
    assert worker.current is None and worker.step is None and worker.issue_title is None


def test_every_finished_run_is_recorded_for_the_cockpit() -> None:
    from github_issue_worker.application.history import RunRecord

    recorded: list[RunRecord] = []
    f = Fixture([slot("claude", observed_model="claude-opus-5-5")], verdicts=[Verdict("merged")])
    f.manager.on_finished = recorded.append
    f.new_issues(issue(1))
    f.manager.tick()
    (run,) = recorded
    assert (run.repository, run.issue, run.worker, run.agent) == (
        "Hawkynt/ShiftDiff",
        1,
        "claude",
        "claude",
    )
    assert (run.model, run.kind, run.verdict) == ("Claude Opus 5.5", "first", "merged")
    assert run.started == T0 and run.finished == T0


def test_an_accounts_cap_counts_every_instance_on_it() -> None:
    # F12.11: two workers on one account capped at 2 run two issues, the third queues.
    workers = [slot("claude"), slot("claude.2", worker="claude"), slot("codex", "codex")]
    f = Fixture(workers, verdicts=[Verdict("paused", until=T0 + timedelta(hours=1))] * 3)
    f.manager.max_runs = lambda account: 2
    f.limits.limited_until = lambda account: None  # type: ignore[method-assign]
    f.new_issues(issue(1), issue(2, minutes=1), issue(3, minutes=2))
    f.manager.tick()
    assert sorted(run.issue for run in f.runs) == [1, 2]
    assert f.host.worker_label[3] == "worker:queued"
    assert {run.worker for run in f.runs} <= {"claude", "claude.2", "codex"}


def split_parent(f: Fixture, *children: Issue) -> None:
    """Issue 1 was split into ``children``: labelled, its claim released with step split."""
    f.host.issues[1] = issue(1, frozenset({"worker:split"}))
    f.host.worker_label[1] = "worker:split"
    claim = POLICY.new_claim(1, Holder(ME, "claude"), T0, "issue/1-x")
    f.store.force_create(claim.replace(state=ClaimState.RELEASED, step="split"))
    for child in children:
        f.host.issues[child.number] = child
        f.host.children.setdefault(1, []).append(child.number)


def sub_issue(number: int, state: str = "open", reason: str | None = None) -> Issue:
    body = sub_issue_body(1, SubIssue(f"Part {number}", "", ()))
    return replace(issue(number), body=body, state=state, state_reason=reason)  # type: ignore[arg-type]


def test_a_split_parent_waits_while_a_sub_issue_is_open() -> None:
    f = Fixture([slot("claude")])
    split_parent(f, sub_issue(2, "closed", "completed"), sub_issue(3))
    f.polls.append(PollResult(issues=(f.host.issues[1], f.host.issues[2])))
    f.manager.tick()
    assert f.runs == []
    assert f.host.posted == [] and f.host.closed == []
    assert f.host.worker_label[1] == "worker:split"


def test_a_split_parent_closes_when_its_last_sub_issue_is_completed() -> None:
    f = Fixture([slot("claude")])
    split_parent(f, sub_issue(2, "closed", "completed"), sub_issue(3, "closed", "completed"))
    merged = f"Merged in https://x/pull/50 (m1).\n\n{MARKER_PREFIX}comment -->"
    f.host.issue_comments.append(Comment(70, 3, OWNER, merged, T0, T0))
    f.polls.append(PollResult(issues=(f.host.issues[3],)))
    f.manager.tick()

    assert f.runs == []
    assert f.host.closed == [1]
    ((number, summary),) = f.host.posted
    assert number == 1 and "#2" in summary and "#3" in summary
    assert "https://x/pull/50" in summary
    assert f.host.worker_label[1] is None


def test_a_sub_issue_not_planned_leaves_the_parent_to_a_person() -> None:
    f = Fixture([slot("claude")])
    split_parent(f, sub_issue(2, "closed", "completed"), sub_issue(3, "closed", "not_planned"))
    f.polls.append(PollResult(issues=(f.host.issues[3],)))
    f.manager.tick()
    assert f.host.closed == [] and f.host.posted == []
    assert f.host.worker_label[1] == "worker:split"


def test_a_trusted_comment_on_a_split_parent_queues_a_rework_run() -> None:
    f = Fixture([slot("claude")])
    split_parent(f, sub_issue(2, "closed", "not_planned"))
    note = Comment(80, 1, OWNER, "Do part 2 here after all.", T0, T0)
    f.host.issue_comments.append(note)
    f.polls.append(PollResult(comments=(note,)))
    f.manager.tick()
    assert f.runs == [Recorded("claude", 1, RunKind.REWORK)]


def test_housekeeping_runs_hourly_with_the_claims_held_here_and_anywhere() -> None:
    f = Fixture([slot("claude")], verdicts=[Verdict("paused", until=T0 + timedelta(hours=2))])
    swept: list[tuple[set[int], set[int]]] = []
    f.manager.sweep = lambda watch, mine, claimed: swept.append((mine, claimed))
    other = POLICY.new_claim(5, Holder("laptop-01ab", "codex"), T0, "issue/5-x")
    f.store.force_create(other)
    f.new_issues(issue(1))
    f.manager.tick()
    assert swept == [({1}, {1, 5})]
    f.manager.tick()
    assert len(swept) == 1  # not again within the hour
    f.clock.advance(timedelta(hours=1))
    f.manager.tick()
    assert len(swept) == 2


def test_a_finished_run_leaves_no_blocker_behind() -> None:
    f = Fixture([slot("claude")])
    original = f.run_issue

    def blocked_then_merged(watch, worker, handle, item, comments, kind):  # type: ignore[no-untyped-def]
        worker.blocker = Blocker(
            "CI's pull request run waits for approval to run",
            approvals=(7,),
        )
        return original(watch, worker, handle, item, comments, kind)

    f.manager.run_issue = blocked_then_merged
    f.new_issues(issue(1))
    f.manager.tick()
    assert f.manager.workers[0].blocker is None
