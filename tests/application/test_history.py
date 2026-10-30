from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from github_issue_worker.application.history import (
    HistoryStore,
    RunRecord,
    activity,
    cumulative_merged,
    daily_merged,
    kpis,
    model_ledger,
    resolve_times,
    weekly_mix,
)

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)


def record(
    issue: int,
    days_ago: float,
    minutes: float = 30,
    verdict: str = "merged",
    model: str = "Claude Opus 5.5",
    agent: str = "claude",
    repository: str = "o/a",
    kind: str = "first",
    issue_since: float | None = None,
) -> RunRecord:
    finished = NOW - timedelta(days=days_ago)
    started = finished - timedelta(minutes=minutes)
    return RunRecord(
        repository=repository,
        issue=issue,
        worker=agent,
        agent=agent,
        model=model,
        kind=kind,
        verdict=verdict,
        started=started,
        finished=finished,
        issue_started=(finished - timedelta(minutes=issue_since)) if issue_since else started,
    )


def test_runs_survive_a_restart(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append(record(1, 1))
    store.append(record(2, 0, verdict="needs-input"))
    again = HistoryStore(tmp_path / "history.jsonl").load()
    assert [(r.issue, r.verdict) for r in again] == [(1, "merged"), (2, "needs-input")]
    assert again[0] == record(1, 1)


def test_a_damaged_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path)
    store.append(record(1, 1))
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{half a line\n")
    store.append(record(2, 0))
    assert [r.issue for r in HistoryStore(path).load()] == [1, 2]


def test_merged_issues_per_day_split_by_model() -> None:
    runs = [
        record(1, 0.1),
        record(2, 0.2, model="GPT 6.1 Sol", agent="codex"),
        record(3, 1.1),
        record(4, 1.2, verdict="needs-input"),  # not merged: not counted
        record(5, 40),  # outside the window
    ]
    days, series = daily_merged(runs, NOW, days=7, by="model")
    assert len(days) == 7 and days[-1] == NOW.date()
    assert series["Claude Opus 5.5"][-1] == 1 and series["GPT 6.1 Sol"][-1] == 1
    assert series["Claude Opus 5.5"][-2] == 1
    assert sum(sum(counts) for counts in series.values()) == 3


def test_the_split_can_be_by_agent_or_repository() -> None:
    runs = [record(1, 0.1, agent="codex"), record(2, 0.2, repository="o/b")]
    assert set(daily_merged(runs, NOW, days=3, by="agent")[1]) == {"codex", "claude"}
    assert set(daily_merged(runs, NOW, days=3, by="repository")[1]) == {"o/a", "o/b"}


def test_more_than_eight_series_fold_into_other() -> None:
    runs = [record(i, 0.1, model=f"Model {i}") for i in range(10)]
    series = daily_merged(runs, NOW, days=2, by="model")[1]
    assert len(series) == 8 and series["Other"][-1] == 3


def test_weekly_mix_counts_every_run_per_model_per_week() -> None:
    runs = [record(1, 0.5), record(2, 8, model="GPT 6.1 Sol"), record(3, 9, verdict="failed")]
    weeks, series = weekly_mix(runs, NOW, weeks=4)
    assert len(weeks) == 4
    assert series["Claude Opus 5.5"][-1] == 1
    assert series["GPT 6.1 Sol"][-2] == 1 and series["Claude Opus 5.5"][-2] == 1


def test_resolve_time_runs_from_the_issues_first_run_to_its_merge() -> None:
    runs = [
        record(1, 0, minutes=30, issue_since=90),
        record(2, 0, minutes=10),
        record(3, 0, minutes=50, model="GPT 6.1 Sol"),
        record(4, 0, verdict="failed", minutes=500),
    ]
    by_model = {row.name: row for row in resolve_times(runs, by="model")}
    assert by_model["Claude Opus 5.5"].count == 2
    assert by_model["Claude Opus 5.5"].average == timedelta(minutes=50)
    assert by_model["Claude Opus 5.5"].median == timedelta(minutes=50)
    assert by_model["GPT 6.1 Sol"].average == timedelta(minutes=50)
    assert [row.name for row in resolve_times(runs, by="repository")] == ["o/a"]


def test_kpis_for_the_top_row() -> None:
    runs = [
        record(1, 0.1, minutes=20),
        record(2, 3, minutes=40),
        record(3, 20, minutes=60),
        record(4, 0.2, verdict="failed"),
    ]
    numbers = kpis(runs, NOW)
    assert (numbers.today, numbers.week, numbers.total) == (1, 2, 3)
    assert numbers.median_resolve == timedelta(minutes=40)
    assert numbers.merge_share == 0.75
    assert len(numbers.last_30_days) == 30 and numbers.last_30_days[-1] == 1


def test_an_issue_started_with_its_first_run_since_the_last_merge(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.jsonl")
    store.append(record(7, 3, minutes=10))  # merged three days ago
    store.append(record(7, 1, minutes=20, verdict="needs-input"))  # reopened, asked a question
    now = NOW
    asked = record(7, 1, minutes=20).started
    assert store.issue_started("o/a", 7, now) == asked
    assert store.issue_started("o/a", 8, now) == now


def test_the_model_ledger_counts_runs_merges_and_time_per_model() -> None:
    runs = [
        record(1, 9, minutes=20),
        record(2, 1, minutes=40),
        record(3, 2, verdict="failed"),
        record(4, 5, model="GPT 6.1 Sol", agent="codex", minutes=10),
    ]
    rows = {row.model: row for row in model_ledger(runs)}
    opus = rows["Claude Opus 5.5"]
    assert (opus.runs, opus.merged, opus.agent) == (3, 2, "claude")
    assert opus.median_resolve == timedelta(minutes=30)
    assert opus.first_used == runs[0].started and opus.last_used == runs[1].finished
    assert [row.model for row in model_ledger(runs)] == ["Claude Opus 5.5", "GPT 6.1 Sol"]


def test_the_running_total_of_merges_and_the_activity_grid() -> None:
    runs = [record(1, 2), record(2, 1, verdict="failed"), record(3, 0)]
    assert [count for _, count in cumulative_merged(runs)] == [1, 2]
    grid = activity(runs)
    assert len(grid) == 7 and all(len(row) == 24 for row in grid)
    assert sum(map(sum, grid)) == 3 and grid[NOW.weekday()][NOW.hour] == 1
