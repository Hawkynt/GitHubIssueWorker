from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from github_issue_worker.ui.charts import (
    Span,
    area_line,
    color_slots,
    donut,
    heatmap,
    horizontal_bars,
    ring_gauge,
    sparkline,
    stacked_columns,
    timeline,
)


def test_colours_follow_the_entity_in_order_of_first_appearance() -> None:
    slots = color_slots(["Claude Opus 5.5", "GPT 6.1 Sol", "Claude Opus 5.5", "Gemini 4"])
    assert slots == {"Claude Opus 5.5": 1, "GPT 6.1 Sol": 2, "Gemini 4": 3}
    assert (
        color_slots(["A"] + [f"M{i}" for i in range(10)])["M8"] == 0
    )  # beyond 8 slots: Other's grey


def test_stacked_columns_draw_one_segment_per_nonzero_value_with_tooltips() -> None:
    axis = [date(2026, 10, 28) + timedelta(days=i) for i in range(3)]
    svg = stacked_columns(axis, {"A": [1, 0, 2], "B": [0, 0, 1]}, {"A": 1, "B": 2}, unit="merged")
    assert svg.count('class="seg s1"') == 2 and svg.count('class="seg s2"') == 1
    assert 'data-tip="2026-10-30: 3 merged' in svg
    assert "<svg" in svg and 'role="img"' in svg


def test_an_empty_chart_says_so() -> None:
    assert "nothing yet" in stacked_columns([date(2026, 10, 30)], {}, {}, unit="merged")


def test_horizontal_bars_are_ranked_and_labelled() -> None:
    svg = horizontal_bars([("slow", 120.0, "2h"), ("fast", 30.0, "30m")])
    assert svg.index("slow") < svg.index("fast")
    widths = [float(w) for w in re.findall(r'class="bar" [^>]*width="([\d.]+)"', svg)]
    assert widths[0] > widths[1] > 0


def test_a_sparkline_is_a_single_path() -> None:
    svg = sparkline([0, 1, 3, 2])
    assert svg.count("<path") == 1


def test_the_newer_charts_draw_their_marks_with_tooltips() -> None:
    start = datetime(2026, 10, 30, 0, tzinfo=UTC)
    end = start + timedelta(hours=12)
    assert "stroke-dasharray" in ring_gauge(0.75)
    ring = donut([("A", 3), ("B", 1)], {"A": 1, "B": 2}, "runs")
    assert ring.count('class="arc') == 2 and 'data-tip="A: 3 (75%)"' in ring
    assert 'data-tip="B: 1 (100%)"' in donut([("B", 1)], {"B": 2}, "runs")  # a whole ring
    area = area_line(
        [(start + timedelta(hours=1), 1), (start + timedelta(hours=5), 2)], start, end, "merged"
    )
    assert "area-line" in area and area.count('class="hit dot"') == 2
    bars = timeline(
        [
            Span(
                "claude",
                start + timedelta(hours=2),
                start + timedelta(hours=3),
                1,
                "run",
                live=True,
            )
        ],
        ["claude"],
        start,
        end,
    )
    assert 'class="run-bar s1 live"' in bars and ">claude<" in bars
    grid = heatmap(
        [[0] * 24 for _ in range(6)] + [[1] + [0] * 23], ["M", "T", "W", "T", "F", "S", "S"]
    )
    assert grid.count('class="cell on"') == 1


def test_empty_charts_keep_their_frame() -> None:
    assert "<svg" in stacked_columns([date(2026, 10, 30)], {}, {}, unit="merged")
    assert "<svg" in timeline(
        [], [], datetime(2026, 10, 30, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    )
