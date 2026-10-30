from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from github_issue_worker.ui.charts import (
    FALLBACK,
    Span,
    cumulative_lines,
    donut,
    heatmap,
    horizontal_bars,
    ring_gauge,
    sparkline,
    stacked_columns,
    timeline,
)

BLUE, ORANGE = "#3987e5", "#d95926"


def test_every_mark_wears_the_colour_it_was_handed() -> None:
    axis = [date(2026, 10, 28) + timedelta(days=i) for i in range(3)]
    svg = stacked_columns(
        axis, {"A": [1, 0, 2], "B": [0, 0, 1]}, {"A": BLUE, "B": ORANGE}, unit="merged"
    )
    assert svg.count(f"--c:{BLUE}") == 2 and svg.count(f"--c:{ORANGE}") == 1
    assert 'data-tip="2026-10-30: 3 merged' in svg
    assert "<svg" in svg and 'role="img"' in svg


def test_an_entity_no_colour_was_worked_out_falls_back_to_grey() -> None:
    axis = [date(2026, 10, 28)]
    svg = stacked_columns(axis, {"A": [1]}, {}, unit="merged")
    assert f"--c:{FALLBACK}" in svg


def test_an_empty_chart_says_so() -> None:
    assert "nothing yet" in stacked_columns([date(2026, 10, 30)], {}, {}, unit="merged")


def test_horizontal_bars_are_ranked_and_may_wear_a_colour() -> None:
    svg = horizontal_bars([("slow", 120.0, "2h"), ("fast", 30.0, "30m")], colors={"slow": ORANGE})
    assert svg.index("slow") < svg.index("fast")
    widths = [float(w) for w in re.findall(r'class="bar" [^>]*width="([\d.]+)"', svg)]
    assert widths[0] > widths[1] > 0
    assert f"--c:{ORANGE}" in svg


def test_a_sparkline_is_a_single_path() -> None:
    svg = sparkline([0, 1, 3, 2])
    assert svg.count("<path") == 1


def test_the_newer_charts_draw_their_marks_with_tooltips() -> None:
    start = datetime(2026, 10, 30, 0, tzinfo=UTC)
    end = start + timedelta(hours=12)
    assert "stroke-dasharray" in ring_gauge(0.75)
    ring = donut([("A", 3), ("B", 1)], {"A": BLUE, "B": ORANGE}, "runs")
    assert ring.count('class="arc') == 2 and 'data-tip="A: 3 (75%)"' in ring
    assert 'data-tip="B: 1 (100%)"' in donut([("B", 1)], {"B": ORANGE}, "runs")  # a whole ring
    lines = cumulative_lines(
        [
            ("Opus", [(start + timedelta(hours=1), 1), (start + timedelta(hours=5), 2)]),
            ("Sol", [(start + timedelta(hours=3), 1)]),
        ],
        start,
        end,
        "merged",
        {"Opus": BLUE, "Sol": ORANGE},
    )
    assert lines.count('class="series-line"') == 2
    assert lines.count('class="hit dot"') == 3
    assert f"--c:{ORANGE}" in lines and 'data-tip="Opus · ' in lines
    bars = timeline(
        [
            Span(
                "claude",
                start + timedelta(hours=2),
                start + timedelta(hours=3),
                BLUE,
                "run",
                live=True,
            )
        ],
        ["claude"],
        start,
        end,
    )
    assert 'class="run-bar live"' in bars and f"--c:{BLUE}" in bars and ">claude<" in bars
    grid = heatmap(
        [[0] * 24 for _ in range(6)] + [[1] + [0] * 23], ["M", "T", "W", "T", "F", "S", "S"]
    )
    assert grid.count('class="cell on"') == 1


def test_empty_charts_keep_their_frame() -> None:
    assert "<svg" in stacked_columns([date(2026, 10, 30)], {}, {}, unit="merged")
    assert "<svg" in timeline(
        [], [], datetime(2026, 10, 30, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    )
