from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.application.status import (
    RepositoryClaims,
    known_managers,
    render_status,
)
from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState, Holder, RunEntry
from github_issue_worker.domain.issue import Actor, Issue

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
ME = "manager@host"


def claim(
    issue: int,
    *,
    manager: str = ME,
    worker: str = "alpha",
    state: ClaimState = ClaimState.WORKING,
    step: str = "implement",
    renewed: timedelta = timedelta(minutes=12),
    paused_until: datetime | None = None,
) -> Claim:
    renewed_at = NOW - renewed
    return Claim(
        issue=issue,
        holder=Holder(manager, worker),
        generation=1,
        state=state,
        step=step,
        claimed_at=renewed_at,
        renewed_at=renewed_at,
        expires_at=renewed_at + POLICY.validity,
        branch=f"issue/{issue}-x",
        paused_until=paused_until,
    )


def slots() -> list[WorkerSlot]:
    return [
        WorkerSlot(name="alpha", agent="claude", account="personal"),
        WorkerSlot(name="beta", agent="codex", account="work"),
    ]


def render(
    repositories: list[RepositoryClaims],
    workers: list[WorkerSlot] | None = None,
    limits: dict[str, datetime] | None = None,
) -> str:
    return render_status(
        ME,
        slots() if workers is None else workers,
        (limits or {}).get,
        repositories,
        POLICY,
        NOW,
    )


def section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if not lines[i]), len(lines))
    return lines[start:end]


def test_the_output_is_headed_sections_separated_by_blank_lines() -> None:
    text = render([RepositoryClaims("o/a", ())])
    blocks = text.rstrip("\n").split("\n\n")
    assert [block.splitlines()[0] for block in blocks] == ["Manager", "Workers", "o/a"]
    assert blocks[0].splitlines()[1] == "  manager@host"


def test_every_worker_is_one_aligned_row_with_its_state() -> None:
    gone = WorkerSlot("gamma-long", "antigravity", "x", available=False, reason="gone")
    workers = [*slots(), gone]
    limits = {"work": NOW + timedelta(days=6, hours=23, minutes=5)}
    rows = section(render([RepositoryClaims("o/a", ())], workers, limits), "Workers")
    assert rows == [
        "  NAME        AGENT        ACCOUNT   MODEL  STATE",
        "  alpha       claude       personal  -      ok",
        "  beta        codex        work      -      limited until 2026-11-06 09:05 UTC"
        " (in 6d 23h)",
        "  gamma-long  antigravity  x         -      unavailable: gone",
    ]


def test_workers_show_the_model_they_ran_on_or_are_configured_for() -> None:
    workers = [
        WorkerSlot("alpha", "claude", "personal", observed_model="claude-opus-5-5"),
        WorkerSlot("beta", "codex", "work", model="gpt-6.1-sol"),
        WorkerSlot("local", "claude", "gpu", observed_model="Qwen3.8-Flash-Next IQ3_S"),
    ]
    text = render([RepositoryClaims("o/a", ())], workers)
    rows = [re.split(r"\s{2,}", row.strip()) for row in section(text, "Workers")]
    models = [row[3] for row in rows[1:]]
    assert models == [
        "Claude Opus 5.5",
        "GPT 6.1 Sol (configured)",
        "Qwen3.8 Flash Next IQ3_S",
    ]


def test_this_managers_claims_show_which_model_works_the_issue() -> None:
    workers = [WorkerSlot("alpha", "claude", "personal", observed_model="claude-opus-5-5")]
    lines = section(render([RepositoryClaims("o/a", (claim(12),))], workers), "o/a")
    assert lines[1].split()[:3] == ["ISSUE", "WORKER", "MODEL"]
    assert re.split(r"\s{2,}", lines[2].strip())[:3] == ["#12", "alpha", "Claude Opus 5.5"]


def test_claims_of_this_manager_show_worker_step_branch_renewal_and_expiry() -> None:
    text = render([RepositoryClaims("o/a", (claim(12),))])
    assert section(text, "o/a") == [
        "  Claims held by this manager",
        "    ISSUE  WORKER  MODEL  STATE    STEP       RENEWED  EXPIRES"
        "                           BRANCH",
        "    #12    alpha   -      working  implement  12m ago  2026-11-06 09:48 UTC (in 6d 23h)"
        "  issue/12-x",
    ]


def test_claims_of_other_managers_are_listed_apart_with_their_holder() -> None:
    claims = (claim(12), claim(14, manager="other@box", worker="w", renewed=timedelta(hours=3)))
    lines = section(render([RepositoryClaims("o/a", claims)]), "o/a")
    mine = lines.index("  Claims held by this manager")
    others = lines.index("  Claims held by other managers")
    assert mine < others
    assert not any("#14" in line for line in lines[mine:others])
    assert lines[others + 1].split() == [
        "ISSUE",
        "HOLDER",
        "STATE",
        "STEP",
        "RENEWED",
        "EXPIRES",
        "BRANCH",
    ]
    assert lines[others + 2].split() == [
        "#14", "other@box/w", "working", "implement", "3h", "0m", "ago",
        "2026-11-06", "07:00", "UTC", "(in", "6d", "21h)", "issue/14-x",
    ]  # fmt: skip


def test_paused_claims_say_until_when() -> None:
    paused = claim(12, state=ClaimState.PAUSED, paused_until=NOW + timedelta(hours=2, minutes=30))
    text = render([RepositoryClaims("o/a", (paused,))])
    assert "paused until 2026-10-30 12:30 UTC (in 2h 30m)" in text


def test_claims_nobody_renewed_for_validity_plus_grace_are_abandoned() -> None:
    stale = claim(12, renewed=timedelta(days=8, hours=1))
    row = section(render([RepositoryClaims("o/a", (stale,))]), "o/a")[2]
    assert row.split()[:4] == ["#12", "alpha", "-", "abandoned"]
    assert "8d 1h ago" in row
    assert "2026-10-29 09:00 UTC (1d 1h ago)" in row


def test_finished_claims_are_only_counted() -> None:
    finished = (
        claim(1, state=ClaimState.DONE),
        claim(2, state=ClaimState.RELEASED),
        claim(3, state=ClaimState.DONE),
    )
    assert section(render([RepositoryClaims("o/a", finished)]), "o/a") == [
        "  no active claims",
        "  3 finished (done/released)",
    ]


def test_a_repository_without_claims_says_so() -> None:
    assert section(render([RepositoryClaims("o/a", ())]), "o/a") == ["  no active claims"]


def test_no_watched_repositories_points_to_add() -> None:
    text = render([])
    assert text.rstrip("\n").splitlines()[-1] == (
        "no repositories watched; add one with 'github-issue-worker add <owner/repo>'"
    )


def test_no_workers_says_so() -> None:
    assert section(render([], workers=[]), "Workers") == ["  no workers configured"]


def test_the_same_claims_and_clock_render_the_same_text() -> None:
    repositories = [RepositoryClaims("o/a", (claim(12), claim(2, state=ClaimState.DONE)))]
    assert render(repositories) == render(repositories)


def test_given_a_claim_with_recorded_model_when_slot_has_none_then_claim_model_is_shown() -> None:
    held = claim(12).replace(model="claude-opus-5-5")
    lines = section(render([RepositoryClaims("o/a", (held,))]), "o/a")
    assert re.split(r"\s{2,}", lines[2].strip())[:3] == ["#12", "alpha", "Claude Opus 5.5"]


def test_given_claims_and_runs_across_managers_when_rendered_then_known_managers_are_listed() -> (
    None
):
    earlier = RunEntry(
        manager="runner@cloud",
        worker="gemini",
        agent="antigravity",
        model="Gemini 4 Argon",
        kind="first",
        verdict="needs-input",
        started=NOW - timedelta(hours=2),
        finished=NOW - timedelta(hours=1),
    )
    claims = (
        claim(12).replace(runs=(earlier,)),
        claim(14, manager="other@box", worker="w", renewed=timedelta(hours=3)),
    )
    managers = known_managers(claims, ME, "idle@laptop")
    assert managers == ("idle@laptop", "manager@host", "other@box", "runner@cloud")
    lines = section(render([RepositoryClaims("o/a", claims, managers=managers)]), "o/a")
    assert lines[0] == "  Known managers: idle@laptop, manager@host, other@box, runner@cloud"


def test_given_a_paused_claim_without_reset_time_when_rendered_then_it_shows_paused() -> None:
    paused = claim(12, state=ClaimState.PAUSED, paused_until=None)
    row = section(render([RepositoryClaims("o/a", (paused,))]), "o/a")[2]
    assert row.split()[:4] == ["#12", "alpha", "-", "paused"]


def test_given_open_unserved_issues_when_rendered_then_each_is_listed_with_routing() -> None:
    unserved = (
        Issue(
            number=19,
            title="GPU benchmark fails",
            body="",
            state="open",
            author=Actor("hawkynt"),
            labels=frozenset({"worker:unserved", "needs:gpu", "agent:claude", "bug"}),
            is_pull_request=False,
            created_at=NOW,
            updated_at=NOW,
        ),
        Issue(
            number=7,
            title="Windows installer path",
            body="",
            state="open",
            author=Actor("hawkynt"),
            labels=frozenset({"worker:unserved", "needs:windows"}),
            is_pull_request=False,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    lines = section(render([RepositoryClaims("o/a", (), unserved=unserved)]), "o/a")
    idx = lines.index("  Unserved issues")
    assert lines[idx + 1 :] == [
        "    ISSUE  ROUTING                  TITLE",
        "    #7     needs:windows            Windows installer path",
        "    #19    agent:claude, needs:gpu  GPU benchmark fails",
    ]
