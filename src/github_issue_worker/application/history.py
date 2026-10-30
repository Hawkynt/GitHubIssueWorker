"""The manager's long-term memory of finished runs, and the numbers the cockpit draws from it.

Every finished run is one JSON line in ``history.jsonl`` in the state directory: append-only, kept
across restarts, and robust to a damaged line. The metrics are pure functions over the records.
"""

from __future__ import annotations

import json
import statistics
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

SplitBy = Literal["model", "agent", "repository"]
MAX_SERIES = 8
"""Categorical slots; a ninth series folds into "Other" instead of getting a generated colour."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    repository: str
    issue: int
    worker: str
    agent: str
    model: str
    kind: str
    verdict: str
    started: datetime
    finished: datetime
    issue_started: datetime
    """When work on the issue began, for the time from first run to merge."""

    def to_json(self) -> str:
        data = asdict(self)
        for key in ("started", "finished", "issue_started"):
            data[key] = data[key].astimezone(UTC).isoformat()
        return json.dumps(data, sort_keys=True)

    @staticmethod
    def from_json(line: str) -> RunRecord:
        data = json.loads(line)
        for key in ("started", "finished", "issue_started"):
            data[key] = datetime.fromisoformat(data[key])
        return RunRecord(**data)


class HistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: RunRecord) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(record.to_json() + "\n")

    def issue_started(self, repository: str, issue: int, default: datetime) -> datetime:
        """The start of the issue's first run since it was last merged, else ``default``."""
        start = default
        for record in self.load():
            if (record.repository, record.issue) != (repository, issue):
                continue
            if record.verdict == "merged":
                start = default
            elif record.started < start:
                start = record.started
        return start

    def load(self) -> list[RunRecord]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(RunRecord.from_json(line))
            except (ValueError, TypeError, KeyError):
                continue
        return records


def _key(record: RunRecord, by: SplitBy) -> str:
    return {"model": record.model, "agent": record.agent, "repository": record.repository}[by]


def _fold(series: dict[str, list[int]]) -> dict[str, list[int]]:
    """At most eight series: the largest seven by total, the rest summed into "Other"."""
    if len(series) <= MAX_SERIES:
        return dict(sorted(series.items(), key=lambda item: (-sum(item[1]), item[0])))
    ranked = sorted(series.items(), key=lambda item: (-sum(item[1]), item[0]))
    kept = dict(ranked[: MAX_SERIES - 1])
    rest = [counts for _, counts in ranked[MAX_SERIES - 1 :]]
    kept["Other"] = [sum(values) for values in zip(*rest, strict=True)]
    return kept


def daily_merged(
    records: list[RunRecord], now: datetime, days: int = 30, by: SplitBy = "model"
) -> tuple[list[date], dict[str, list[int]]]:
    """Issues merged per day over the last ``days`` days, split by model, agent or repository."""
    today = now.astimezone(UTC).date()
    axis = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    index = {day: position for position, day in enumerate(axis)}
    series: dict[str, list[int]] = defaultdict(lambda: [0] * days)
    for record in records:
        day = record.finished.astimezone(UTC).date()
        if record.verdict == "merged" and day in index:
            series[_key(record, by)][index[day]] += 1
    return axis, _fold(dict(series))


def weekly_mix(
    records: list[RunRecord], now: datetime, weeks: int = 26
) -> tuple[list[date], dict[str, list[int]]]:
    """Runs per model per week (Monday-based), whatever their verdict: which models did the work."""
    today = now.astimezone(UTC).date()
    this_monday = today - timedelta(days=today.weekday())
    axis = [this_monday - timedelta(weeks=offset) for offset in range(weeks - 1, -1, -1)]
    index = {monday: position for position, monday in enumerate(axis)}
    series: dict[str, list[int]] = defaultdict(lambda: [0] * weeks)
    for record in records:
        day = record.finished.astimezone(UTC).date()
        monday = day - timedelta(days=day.weekday())
        if monday in index:
            series[record.model][index[monday]] += 1
    return axis, _fold(dict(series))


@dataclass(frozen=True, slots=True)
class ResolveTime:
    name: str
    count: int
    average: timedelta
    median: timedelta


def resolve_times(records: list[RunRecord], by: SplitBy = "model") -> list[ResolveTime]:
    """From an issue's first run to its merge, per model, agent or repository; slowest first."""
    durations: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.verdict == "merged":
            seconds = (record.finished - record.issue_started).total_seconds()
            durations[_key(record, by)].append(max(seconds, 0.0))
    rows = [
        ResolveTime(
            name,
            len(values),
            timedelta(seconds=statistics.fmean(values)),
            timedelta(seconds=statistics.median(values)),
        )
        for name, values in durations.items()
    ]
    return sorted(rows, key=lambda row: (-row.average, row.name))


@dataclass(frozen=True, slots=True)
class Kpis:
    today: int
    week: int
    total: int
    median_resolve: timedelta | None
    merge_share: float | None
    """Merged runs among all finished runs that ended an issue's work for now."""
    last_30_days: list[int]


def kpis(records: list[RunRecord], now: datetime) -> Kpis:
    today = now.astimezone(UTC).date()
    merged = [r for r in records if r.verdict == "merged"]
    days = [r.finished.astimezone(UTC).date() for r in merged]
    concluded = [r for r in records if r.verdict in ("merged", "failed")]
    durations = [(r.finished - r.issue_started).total_seconds() for r in merged]
    _, series = daily_merged(records, now, days=30, by="model")
    per_day = [sum(values) for values in zip(*series.values(), strict=True)] if series else [0] * 30
    return Kpis(
        today=sum(1 for day in days if day == today),
        week=sum(1 for day in days if (today - day).days < 7),
        total=len(merged),
        median_resolve=timedelta(seconds=statistics.median(durations)) if durations else None,
        merge_share=len(merged) / len(concluded) if concluded else None,
        last_30_days=per_day,
    )


@dataclass(frozen=True, slots=True)
class LedgerRow:
    model: str
    agent: str
    """The agent that ran the model most recently."""
    runs: int
    merged: int
    median_resolve: timedelta | None
    first_used: datetime
    last_used: datetime


def model_ledger(records: list[RunRecord]) -> list[LedgerRow]:
    """Every model ever used, with how often and how well; most runs first."""
    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        grouped[record.model].append(record)
    rows = []
    for model, runs in grouped.items():
        merged = [r for r in runs if r.verdict == "merged"]
        durations = [(r.finished - r.issue_started).total_seconds() for r in merged]
        latest = max(runs, key=lambda r: r.finished)
        rows.append(
            LedgerRow(
                model=model,
                agent=latest.agent,
                runs=len(runs),
                merged=len(merged),
                median_resolve=timedelta(seconds=statistics.median(durations))
                if durations
                else None,
                first_used=min(r.started for r in runs),
                last_used=latest.finished,
            )
        )
    return sorted(rows, key=lambda row: (-row.runs, row.model))


def cumulative_merged(records: list[RunRecord]) -> list[tuple[datetime, int]]:
    """Every merge in time order with the running total after it."""
    merged = sorted(r.finished for r in records if r.verdict == "merged")
    return [(moment, count) for count, moment in enumerate(merged, start=1)]


def activity(records: list[RunRecord]) -> list[list[int]]:
    """Runs finished per weekday (Monday first) and hour of the day, in UTC."""
    matrix = [[0] * 24 for _ in range(7)]
    for record in records:
        moment = record.finished.astimezone(UTC)
        matrix[moment.weekday()][moment.hour] += 1
    return matrix
