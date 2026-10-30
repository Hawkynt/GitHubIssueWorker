from __future__ import annotations

from pathlib import Path

from github_issue_worker.domain.claim import ClaimState
from github_issue_worker.domain.issue import Comment
from github_issue_worker.domain.outcome import Outcome, Question
from github_issue_worker.domain.work import RunKind
from github_issue_worker.ports.agents import AgentRun, RunReport
from github_issue_worker.product import MARKER_PREFIX
from tests.application.test_issue_run import OWNER, T0, Harness, done, ok, step

RESTATEMENT = (
    "Goal: retry a timed-out request up to the configured count.\n\n"
    "Acceptance:\n- Given retries=3 and three timeouts, When a request runs, Then 4 attempts."
)


def understood(labels: tuple[str, ...] = ("bug",)) -> RunReport:
    return ok(Outcome("done", "Understood.", restatement=RESTATEMENT, labels=labels))


def test_a_first_run_restates_the_issue_before_any_work(tmp_path: Path) -> None:
    order: list[str] = []

    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        def understand(run: AgentRun) -> RunReport:
            order.append("understand")
            return understood()

        def implement(run: AgentRun) -> RunReport:
            order.append(f"implement after {len(h.host.posted)} comment(s)")
            h.workspace.add_commit(42, "# retries\n\nBody.")
            return ok()

        return [understand, implement]

    h = Harness(tmp_path, setup)
    verdict = h.run(understand_first=True)

    assert verdict.kind == "merged"
    assert order == ["understand", "implement after 1 comment(s)"]
    first_prompt, second_prompt = (run.prompt for run in h.agent.runs)
    assert "do not change any file" in first_prompt.lower()
    assert "restatement" in first_prompt
    assert RESTATEMENT in second_prompt  # the agreed understanding drives the implementation
    issue, body = h.host.posted[0]
    assert issue == 42 and RESTATEMENT in body
    assert f"{MARKER_PREFIX}understanding" in body
    assert h.host.added_labels[42] == ["bug"]
    assert h.agent.runs[1].session == "s1"  # the same conversation continues


def test_a_request_too_vague_to_restate_is_asked_about_before_any_work(tmp_path: Path) -> None:
    question = Question("Retry on which errors?", ("timeouts only", "all 5xx"), "timeouts only")
    vague = ok(
        Outcome("needs-input", "The issue does not say which errors.", questions=(question,))
    )
    h = Harness(tmp_path, lambda h: [step(h.workspace, vague, commit=None)])
    verdict = h.run(understand_first=True)

    assert verdict.kind == "needs-input"
    assert len(h.agent.runs) == 1
    assert "Retry on which errors?" in h.host.posted[-1][1]
    current = h.store.current(42)
    assert current is not None and current.state is ClaimState.RELEASED


def test_an_existing_understanding_is_reused_not_restated(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    earlier = Comment(9, 42, OWNER, f"{RESTATEMENT}\n\n{MARKER_PREFIX}understanding -->", T0, T0)
    h.host.issue_comments.append(earlier)
    assert h.run(understand_first=True).kind == "merged"
    assert len(h.agent.runs) == 1
    assert RESTATEMENT in h.agent.runs[0].prompt


def test_rework_runs_do_not_restate(tmp_path: Path) -> None:
    h = Harness(tmp_path, lambda h: [step(h.workspace, ok())])
    assert h.run(RunKind.REWORK, understand_first=True).kind == "merged"
    assert len(h.agent.runs) == 1


def test_an_understanding_without_a_restatement_is_not_accepted(tmp_path: Path) -> None:
    def setup(h: Harness) -> list:  # type: ignore[type-arg]
        return [
            step(h.workspace, ok(done()), commit=None),
            step(h.workspace, understood(), None),
            step(h.workspace, ok()),
        ]

    h = Harness(tmp_path, setup)
    assert h.run(understand_first=True).kind == "merged"
    assert "restatement" in h.agent.runs[1].prompt
    assert RESTATEMENT in h.host.posted[0][1]
