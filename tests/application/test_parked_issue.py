from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

from github_issue_worker.application.coordinator import ClaimHandle
from github_issue_worker.application.issue_run import IssueRun, RunSettings, Verdict
from github_issue_worker.application.manager import RepositoryWatch, WorkerSlot
from github_issue_worker.application.poller import PollResult
from github_issue_worker.domain.claim import ClaimState
from github_issue_worker.domain.issue import Actor, Comment, Issue
from github_issue_worker.domain.outcome import Outcome, Question
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import RunReport
from tests.application.run_fakes import FakeWorkspace, ScriptedAgent
from tests.application.test_manager import OWNER, T0, Fixture, issue, slot


class ParkedIssue:
    def __init__(
        self, tmp_path: Path, outcome: Literal["needs-input", "impossible"] = "needs-input"
    ) -> None:
        self.f = Fixture([slot("worker")])
        self.workspace = FakeWorkspace(tmp_path)
        self.agent = ScriptedAgent(
            [
                lambda run: RunReport(
                    session="s1",
                    outcome=Outcome(
                        outcome,
                        "Please clarify.",
                        questions=(Question("Which behavior?", ("A", "B"), "A"),),
                    ),
                ),
                lambda run: RunReport(
                    session="s1", outcome=Outcome("needs-input", "One more question.")
                ),
            ]
        )
        runner = IssueRun(
            host=self.f.host,
            workspace=self.workspace,
            coordinator=self.f.coordinator,
            agent=self.agent,
            verify=lambda tree: (True, ""),
            sessions={},
            clock=self.f.clock,
            sleep=lambda seconds: self.f.clock.advance(timedelta(seconds=seconds)),
            settings=RunSettings(understand_first=False),
        )

        def run(
            watch: RepositoryWatch,
            worker: WorkerSlot,
            handle: ClaimHandle,
            item: Issue,
            comments: tuple[Comment, ...],
            kind: RunKind,
        ) -> Verdict:
            return runner.run(handle, item, comments, kind)

        self.f.manager.run_issue = run
        self.original = Comment(10, 1, OWNER, "Original input", T0, T0)
        self.f.host.issue_comments = [self.original]
        self.f.new_issues(issue(1))
        self.f.manager.tick()
        self.checkpoint = self.f.store.read(1)
        assert self.checkpoint is not None
        assert self.checkpoint.claim.state is ClaimState.RELEASED
        assert self.checkpoint.claim.step == "needs-input"
        assert self.checkpoint.claim.consumed_through.comment == 10
        assert len(self.agent.runs) == 1
        assert len(self.f.host.posted) == 1
        self.posts = list(self.f.host.posted)

    def assert_silent(self) -> None:
        assert self.f.store.read(1) == self.checkpoint
        assert self.f.host.posted == self.posts
        assert len(self.agent.runs) == 1
        assert self.f.host.worker_label[1] == "worker:needs-input"
        assert self.workspace.removed == []


@pytest.mark.parametrize("outcome", ["needs-input", "impossible"])
def test_given_a_parked_issue_when_repeatedly_polled_then_no_ping_or_run(
    tmp_path: Path,
    outcome: Literal["needs-input", "impossible"],
) -> None:
    parked = ParkedIssue(tmp_path, outcome)
    for _ in range(3):
        parked.f.new_issues(issue(1))
        parked.f.manager.tick()
        parked.assert_silent()


@pytest.mark.parametrize("days", [7, 8, 365])
def test_given_a_parked_issue_when_lease_intervals_pass_then_no_claim_or_ping(
    tmp_path: Path,
    days: int,
) -> None:
    parked = ParkedIssue(tmp_path)
    parked.f.clock.advance(timedelta(days=days))
    parked.f.new_issues(issue(1))
    parked.f.manager.tick()
    parked.assert_silent()


@pytest.mark.parametrize("manager_id", ["office-pc-7f3a", "another-machine"])
def test_given_a_persisted_parked_issue_when_a_fresh_manager_polls_then_it_stays_silent(
    tmp_path: Path,
    manager_id: str,
) -> None:
    parked = ParkedIssue(tmp_path)
    fresh = Fixture([slot("worker")]).manager
    fresh.manager_id = manager_id
    fresh.repositories = [parked.f.watch]
    fresh.poll = parked.f.poll
    fresh.clock = parked.f.clock
    fresh.run_issue = parked.f.manager.run_issue
    parked.f.clock.advance(timedelta(days=30))
    for _ in range(3):
        parked.f.new_issues(issue(1))
        fresh.tick()
        parked.assert_silent()


@pytest.mark.parametrize(
    "event", ["consumed", "own-output", "other-manager", "untrusted", "metadata"]
)
def test_given_a_parked_issue_when_only_ignored_events_arrive_then_no_ping_or_run(
    tmp_path: Path,
    event: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parked = ParkedIssue(tmp_path)
    f = parked.f
    monkeypatch.setattr(f.host, "role", lambda login: "admin" if login == OWNER.login else "read")
    comment = {
        "consumed": replace(parked.original, body="Edited old input"),
        "own-output": Comment(11, 1, OWNER, "<!-- ghiw:question -->", T0, T0),
        "other-manager": Comment(12, 1, OWNER, "<!-- ghiw:another-machine -->", T0, T0),
        "untrusted": Comment(13, 1, Actor("outsider"), "Answer", T0, T0),
    }.get(event)
    if comment:
        f.host.issue_comments = [comment]
        f.polls.append(PollResult(comments=(comment,)))
    else:
        f.new_issues(replace(issue(1), title="Edited title", updated_at=T0 + timedelta(hours=1)))
    f.manager.tick()
    parked.assert_silent()


def test_given_a_parked_issue_when_a_trusted_answer_arrives_then_one_rework_consumes_it(
    tmp_path: Path,
) -> None:
    parked = ParkedIssue(tmp_path)
    f = parked.f
    f.clock.advance(timedelta(minutes=1))
    answer = Comment(11, 1, OWNER, "Use A", f.clock.now(), f.clock.now())
    f.host.issue_comments.append(answer)
    f.polls.append(PollResult(comments=(answer,)))
    f.manager.tick()
    assert len(parked.agent.runs) == 2
    assert len(f.host.posted) == 2
    assert "Use A" in parked.agent.runs[1].prompt
    current = f.store.current(1)
    assert current is not None
    assert current.branch == parked.checkpoint.claim.branch
    assert current.consumed_through.comment == 11
    assert current.state is ClaimState.RELEASED and current.step == "needs-input"
    assert current.runs[-1].kind == RunKind.REWORK
    checkpoint = f.store.read(1)
    for _ in range(3):
        f.polls.append(PollResult(comments=(answer,)))
        f.manager.tick()
        assert f.store.read(1) == checkpoint
        assert len(parked.agent.runs) == 2
        assert len(f.host.posted) == 2
