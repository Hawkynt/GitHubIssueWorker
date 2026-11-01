from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.application.status import RepositoryClaims, render_status
from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState, Holder

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
    accounts: tuple[str, ...] = (),
    now: datetime = NOW,
) -> str:
    return render_status(
        ME,
        slots() if workers is None else workers,
        (limits or {}).get,
        repositories,
        POLICY,
        now,
        accounts,
    )


def section(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if not lines[i]), len(lines))
    return lines[start:end]


def test_the_output_is_headed_sections_separated_by_blank_lines() -> None:
    text = render([RepositoryClaims("o/a", ())])
    blocks = text.rstrip("\n").split("\n\n")
    assert [block.splitlines()[0] for block in blocks] == [
        "Manager",
        "Workers",
        "Accounts",
        "o/a",
    ]
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


# --- F13.7: each account's state, reset time and the issues waiting on it ------------------------


def account_rows(text: str) -> dict[str, list[str]]:
    rows = section(text, "Accounts")
    assert re.split(r"\s{2,}", rows[0].strip()) == ["ACCOUNT", "STATE", "RESETS", "WAITING"]
    return {cells[0]: cells[1:] for cells in (re.split(r"\s{2,}", r.strip()) for r in rows[1:])}


def anthropic_slots() -> list[WorkerSlot]:
    return [
        WorkerSlot(name="claude", agent="claude", account="anthropic-max", worker="claude"),
        WorkerSlot(name="claude.2", agent="claude", account="anthropic-max", worker="claude"),
        WorkerSlot(name="codex", agent="codex", account="openai-plus"),
    ]


def test_configured_accounts_without_a_limit_are_available_with_nothing_waiting() -> None:
    text = render([RepositoryClaims("o/a", ())], [], accounts=("anthropic-max", "openai-plus"))
    assert account_rows(text) == {
        "anthropic-max": ["available", "-", "-"],
        "openai-plus": ["available", "-", "-"],
    }


def test_a_limited_account_shows_its_reset_time() -> None:
    now = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
    limits = {"anthropic-max": datetime(2026, 11, 6, 10, 0, tzinfo=UTC)}
    text = render(
        [RepositoryClaims("o/a", ())],
        anthropic_slots(),
        limits,
        ("anthropic-max", "openai-plus"),
        now,
    )
    rows = account_rows(text)
    assert rows["anthropic-max"] == ["limited", "2026-11-06 10:00 UTC (in 6d 23h)", "-"]
    assert rows["openai-plus"] == ["available", "-", "-"]


def test_a_paused_claim_waits_on_the_account_of_the_instance_holding_it() -> None:
    paused = claim(
        219, worker="claude.2", state=ClaimState.PAUSED, paused_until=NOW + timedelta(hours=1)
    )
    working = claim(220, worker="codex")
    text = render(
        [RepositoryClaims("owner/repo", (paused, working))],
        anthropic_slots(),
        {"anthropic-max": NOW + timedelta(hours=1)},
        ("anthropic-max", "openai-plus"),
    )
    rows = account_rows(text)
    assert rows["anthropic-max"][2] == "owner/repo#219"
    assert rows["openai-plus"][2] == "-"


def test_an_account_whose_limit_has_passed_is_available_again() -> None:
    text = render(
        [RepositoryClaims("o/a", ())],
        anthropic_slots(),
        {"anthropic-max": NOW - timedelta(minutes=5)},
        ("anthropic-max",),
    )
    assert account_rows(text)["anthropic-max"] == ["available", "-", "-"]


def test_a_paused_claim_of_another_manager_waits_on_no_local_account() -> None:
    elsewhere = claim(
        219,
        manager="laptop@home",
        worker="claude.2",
        state=ClaimState.PAUSED,
        paused_until=NOW + timedelta(hours=1),
    )
    text = render(
        [RepositoryClaims("owner/repo", (elsewhere,))],
        anthropic_slots(),
        accounts=("anthropic-max",),
    )
    assert all(row[2] == "-" for row in account_rows(text).values())


def test_accounts_named_only_by_a_worker_are_listed_after_the_configured_ones() -> None:
    text = render([RepositoryClaims("o/a", ())], accounts=("work", "spare"))
    assert list(account_rows(text)) == ["work", "spare", "personal"]


def test_several_issues_waiting_on_one_account_are_listed_in_order() -> None:
    pause = {"state": ClaimState.PAUSED, "paused_until": NOW + timedelta(hours=1)}
    text = render(
        [
            RepositoryClaims("o/b", (claim(3, worker="claude", **pause),)),  # type: ignore[arg-type]
            RepositoryClaims("o/a", (claim(9, worker="claude.2", **pause),)),  # type: ignore[arg-type]
        ],
        anthropic_slots(),
        accounts=("anthropic-max",),
    )
    assert account_rows(text)["anthropic-max"][2] == "o/a#9, o/b#3"
