"""Charts as inline SVG, drawn on the server: no library, no network, works offline.

Mark specs follow the dataviz method: thin marks, a 2px surface gap between stacked segments,
4px rounded data ends, recessive grid and axes, a hover target larger than the mark, and colour
that follows the entity (its slot by first appearance), never its rank.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape

SLOTS = 8


def color_slots(names_in_order_of_appearance: Iterable[str]) -> dict[str, int]:
    """Slot 1..8 per entity in order of first appearance; later ones get 0 (Other's grey)."""
    slots: dict[str, int] = {}
    for name in names_in_order_of_appearance:
        if name not in slots:
            slots[name] = len(slots) + 1 if len(slots) < SLOTS else 0
    return slots


def _rounded_top(x: float, y: float, width: float, height: float, radius: float = 4.0) -> str:
    """A bar whose top end is rounded and whose base sits square on the baseline."""
    r = min(radius, width / 2, height)
    return (
        f"M{x:.1f},{y + height:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + width - r:.1f} Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + r:.1f} "
        f"V{y + height:.1f} Z"
    )


def _nice_max(value: int) -> int:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step * 4:
            return step * 4
    return value


def stacked_columns(
    axis: Sequence[date],
    series: Mapping[str, Sequence[int]],
    slots: Mapping[str, int],
    unit: str,
    width: int = 960,
    height: int = 260,
    prefix: str = "",
) -> str:
    """One column per day (or week), segments stacked per series in the given order."""
    totals = [sum(values[i] for values in series.values()) for i in range(len(axis))]
    if not series or not any(totals):
        return _empty_plot(width, height, f"nothing yet: the first {unit} issue lands here")
    left, bottom, top = 34, 26, 10
    plot_w, plot_h = width - left - 8, height - bottom - top
    peak = _nice_max(max(totals))
    step = plot_w / len(axis)
    bar_w = max(step * 0.62, 2.0)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(unit)} per period" '
        'class="chart" preserveAspectRatio="xMidYMid meet">'
    ]
    for tick in range(5):
        value = peak * tick / 4
        y = top + plot_h - plot_h * tick / 4
        parts.append(f'<line class="grid" x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{value:g}</text>'
        )
    for index, day in enumerate(axis):
        x = left + index * step + (step - bar_w) / 2
        base: float = top + plot_h
        stack = [(name, values[index]) for name, values in series.items() if values[index]]
        for position, (name, count) in enumerate(stack):
            segment = plot_h * count / peak
            gap = 2.0 if position < len(stack) - 1 else 0.0
            if position == len(stack) - 1:
                shape = _rounded_top(x, base - segment, bar_w, segment)
                parts.append(
                    f'<path class="seg s{slots.get(name, 0)}" style="--i:{index}" d="{shape}"/>'
                )
            else:
                parts.append(
                    f'<rect class="seg s{slots.get(name, 0)}" style="--i:{index}" x="{x:.1f}" '
                    f'y="{base - segment + gap:.1f}" '
                    f'width="{bar_w:.1f}" height="{max(segment - gap, 0.5):.1f}"/>'
                )
            base -= segment
        every = max(len(axis) // 6, 1)
        last = index == len(axis) - 1
        if last or (index % every == 0 and len(axis) - 1 - index >= every / 2):
            parts.append(
                f'<text class="tick" x="{x + bar_w / 2:.1f}" y="{height - 8}" text-anchor="middle">'
                f"{day:%b %d}</text>"
            )
        detail = "; ".join(f"{name} {count}" for name, count in stack) or "none"
        tip = escape(f"{prefix}{day:%Y-%m-%d}: {totals[index]} {unit} ({detail})", quote=True)
        parts.append(
            f'<rect class="hit" tabindex="0" x="{left + index * step:.1f}" y="{top}" '
            f'width="{step:.1f}" height="{plot_h}" data-tip="{tip}" aria-label="{tip}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def horizontal_bars(
    rows: Sequence[tuple[str, float, str]], width: int = 460, row_height: int = 30
) -> str:
    """Ranked bars in one hue: name, value and its printed label per row, largest first."""
    if not rows:
        return _empty_plot(width, 120, "times appear with the first merged issue")
    label_w, height = 170, row_height * len(rows) + 6
    peak = max(value for _, value, _ in rows) or 1.0
    plot_w = width - label_w - 150
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="ranked bars" class="chart">'
    ]
    for index, (name, value, label) in enumerate(rows):
        y = 4 + index * row_height
        bar = max(plot_w * value / peak, 3.0)
        tip = escape(f"{name}: {label}", quote=True)
        parts.append(
            f'<text class="row-name" x="{label_w - 10}" y="{y + 17}" text-anchor="end">'
            f"{escape(name if len(name) <= 24 else name[:23] + '…')}</text>"
        )
        parts.append(
            f'<rect class="bar" style="--i:{index}" x="{label_w}" y="{y + 6}" width="{bar:.1f}" height="14" rx="4"/>'
        )
        parts.append(
            f'<text class="row-value" x="{label_w + bar + 8:.1f}" y="{y + 17}">{escape(label)}</text>'
        )
        parts.append(
            f'<rect class="hit" tabindex="0" x="0" y="{y}" width="{width}" height="{row_height}" '
            f'data-tip="{tip}" aria-label="{tip}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def sparkline(values: Sequence[int], width: int = 160, height: int = 34) -> str:
    peak = max(values) if values and max(values) else 1
    step = width / max(len(values) - 1, 1)
    points = " L".join(
        f"{i * step:.1f},{height - 3 - (height - 6) * v / peak:.1f}" for i, v in enumerate(values)
    )
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<path d="M{points}"/></svg>'
    )


def _time_ticks(start: datetime, end: datetime, count: int = 6) -> list[datetime]:
    seconds = max((end - start).total_seconds(), 1.0)
    return [start + timedelta(seconds=seconds * i / count) for i in range(count + 1)]


def _time_label(moment: datetime, start: datetime, end: datetime) -> str:
    return f"{moment:%H:%M}" if end - start <= timedelta(days=2) else f"{moment:%b %d}"


def ring_gauge(fraction: float | None, size: int = 96) -> str:
    """A ring that fills clockwise to ``fraction``; empty when there is nothing to measure."""
    radius = size / 2 - 7
    circumference = 2 * math.pi * radius
    filled = circumference * min(max(fraction or 0.0, 0.0), 1.0)
    return (
        f'<svg class="gauge" viewBox="0 0 {size} {size}" aria-hidden="true">'
        f'<circle class="gauge-track" cx="{size / 2}" cy="{size / 2}" r="{radius:.1f}"/>'
        f'<circle class="gauge-fill" cx="{size / 2}" cy="{size / 2}" r="{radius:.1f}" '
        f'stroke-dasharray="{filled:.1f} {circumference:.1f}" '
        f'style="--length:{circumference:.1f}" transform="rotate(-90 {size / 2} {size / 2})"/>'
        "</svg>"
    )


def _arc(cx: float, cy: float, outer: float, inner: float, start: float, end: float) -> str:
    def point(radius: float, angle: float) -> str:
        return f"{cx + radius * math.sin(angle):.2f},{cy - radius * math.cos(angle):.2f}"

    large = 1 if end - start > math.pi else 0
    return (
        f"M{point(outer, start)} A{outer},{outer} 0 {large} 1 {point(outer, end)} "
        f"L{point(inner, end)} A{inner},{inner} 0 {large} 0 {point(inner, start)} Z"
    )


def donut(
    rows: Sequence[tuple[str, int]], slots: Mapping[str, int], caption: str, size: int = 240
) -> str:
    """Shares of a whole as a ring of arcs with a 2px gap, the total in its centre."""
    total = sum(value for _, value in rows)
    centre, outer, inner = size / 2, size / 2 - 6, size / 2 - 38
    parts = [
        f'<svg class="chart donut" viewBox="0 0 {size} {size}" role="img" aria-label="{escape(caption)}">'
    ]
    parts.append(
        f'<circle class="donut-track" cx="{centre}" cy="{centre}" r="{(outer + inner) / 2}"/>'
    )
    angle, gap = 0.0, (0.035 if len(rows) > 1 else 0.0)
    for index, (name, value) in enumerate(rows):
        if not value:
            continue
        sweep = 2 * math.pi * value / total
        end = angle + sweep - gap
        if sweep >= 2 * math.pi - 1e-6:
            end = angle + 2 * math.pi - 1e-4
        tip = escape(f"{name}: {value} ({value / total:.0%})", quote=True)
        parts.append(
            f'<path class="arc s{slots.get(name, 0)}" style="--i:{index}" '
            f'd="{_arc(centre, centre, outer, inner, angle, end)}" tabindex="0" '
            f'data-tip="{tip}" aria-label="{tip}"/>'
        )
        angle += sweep
    parts.append(
        f'<text class="donut-value" x="{centre}" y="{centre + 4}" text-anchor="middle">{total}</text>'
        f'<text class="donut-caption" x="{centre}" y="{centre + 26}" text-anchor="middle">'
        f"{escape(caption)}</text></svg>"
    )
    return "".join(parts)


def area_line(
    points: Sequence[tuple[datetime, int]],
    start: datetime,
    end: datetime,
    unit: str,
    width: int = 600,
    height: int = 240,
) -> str:
    """A running total as a stepped line over a glowing area, its head pulsing at ``end``."""
    if not points:
        return _empty_plot(width, height, f"the first {unit} issue starts the line")
    left, right, top, bottom = 34, 14, 14, 26
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = _nice_max(max(value for _, value in points))
    seconds = max((end - start).total_seconds(), 1.0)

    def x(moment: datetime) -> float:
        return left + plot_w * (moment - start).total_seconds() / seconds

    def y(value: int) -> float:
        return top + plot_h - plot_h * value / peak

    path = [f"M{left},{y(0):.1f}"]
    previous = 0
    for moment, value in points:
        path.append(f"H{x(moment):.1f} V{y(value):.1f}")
        previous = value
    path.append(f"H{left + plot_w}")
    line = " ".join(path)
    parts = [
        f'<svg class="chart area" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(unit)} issues over time">'
        '<defs><linearGradient id="glow" x1="0" x2="0" y1="0" y2="1">'
        '<stop offset="0" stop-color="#3987e5" stop-opacity=".55"/>'
        '<stop offset="1" stop-color="#3987e5" stop-opacity="0"/></linearGradient></defs>'
    ]
    for tick in range(5):
        ty = top + plot_h - plot_h * tick / 4
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{left - 6}" y="{ty + 4:.1f}" text-anchor="end">{peak * tick / 4:g}</text>'
        )
    for moment in _time_ticks(start, end, 4):
        parts.append(
            f'<text class="tick" x="{x(moment):.1f}" y="{height - 8}" text-anchor="middle">'
            f"{_time_label(moment, start, end)}</text>"
        )
    parts.append(f'<path class="area-fill" d="{line} V{y(0):.1f} Z"/>')
    parts.append(f'<path class="area-line" pathLength="1" d="{line}"/>')
    for moment, value in points:
        tip = escape(f"{moment:%Y-%m-%d %H:%M} UTC: {value} {unit}", quote=True)
        parts.append(
            f'<circle class="hit dot" cx="{x(moment):.1f}" cy="{y(value):.1f}" r="7" tabindex="0" '
            f'data-tip="{tip}" aria-label="{tip}"/>'
        )
    parts.append(
        f'<circle class="head" cx="{left + plot_w:.1f}" cy="{y(previous):.1f}" r="4"/>'
        f'<circle class="head-pulse" cx="{left + plot_w:.1f}" cy="{y(previous):.1f}" r="4"/></svg>'
    )
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class Span:
    """One bar of a timeline: a run from ``start`` to ``end`` on a lane."""

    lane: str
    start: datetime
    end: datetime
    slot: int
    tip: str
    live: bool = False


def timeline(
    spans: Sequence[Span], lanes: Sequence[str], start: datetime, end: datetime, width: int = 960
) -> str:
    """Runs as bars on one lane per worker, with the present as a line on the right."""
    if not lanes:
        return _empty_plot(width, 160, "no runs in this window yet")
    label_w, right, top, lane_h = 110, 14, 8, 30
    height = top + lane_h * len(lanes) + 26
    plot_w = width - label_w - right
    seconds = max((end - start).total_seconds(), 1.0)

    def x(moment: datetime) -> float:
        clamped = min(max(moment, start), end)
        return label_w + plot_w * (clamped - start).total_seconds() / seconds

    parts = [
        f'<svg class="chart timeline" viewBox="0 0 {width} {height}" role="img" aria-label="runs per worker">'
    ]
    for moment in _time_ticks(start, end, 8):
        parts.append(
            f'<line class="grid" x1="{x(moment):.1f}" x2="{x(moment):.1f}" y1="{top}" y2="{height - 22}"/>'
        )
        parts.append(
            f'<text class="tick" x="{x(moment):.1f}" y="{height - 6}" text-anchor="middle">'
            f"{_time_label(moment, start, end)}</text>"
        )
    row = {lane: index for index, lane in enumerate(lanes)}
    for index, lane in enumerate(lanes):
        ly = top + index * lane_h
        parts.append(
            f'<text class="row-name" x="{label_w - 12}" y="{ly + 19}" text-anchor="end">{escape(lane)}</text>'
        )
        parts.append(
            f'<line class="lane-rule" x1="{label_w}" x2="{width - right}" y1="{ly + 15}" y2="{ly + 15}"/>'
        )
    for order, span in enumerate(spans):
        if span.end < start or span.start > end:
            continue
        ly = top + row[span.lane] * lane_h
        x0, x1 = x(span.start), x(span.end)
        tip = escape(span.tip, quote=True)
        parts.append(
            f'<rect class="run-bar s{span.slot}{" live" if span.live else ""}" style="--i:{order}" '
            f'x="{x0:.1f}" y="{ly + 7}" width="{max(x1 - x0, 4):.1f}" height="16" rx="4" '
            f'tabindex="0" data-tip="{tip}" aria-label="{tip}"/>'
        )
    parts.append(
        f'<line class="now" x1="{label_w + plot_w}" x2="{label_w + plot_w}" y1="{top - 4}" y2="{height - 22}"/>'
        "</svg>"
    )
    return "".join(parts)


def heatmap(matrix: Sequence[Sequence[int]], rows: Sequence[str], width: int = 600) -> str:
    """Counts per weekday and hour, one hue from faint to full; the peak cell is the brightest."""
    label_w, top, gap = 40, 4, 3
    cell = (width - label_w) / 24
    height = top + cell * len(rows) + 22
    peak = max((value for line in matrix for value in line), default=0) or 1
    parts = [
        f'<svg class="chart heat" viewBox="0 0 {width} {height:.0f}" role="img" aria-label="activity by weekday and hour">'
    ]
    for r, name in enumerate(rows):
        y = top + r * cell
        parts.append(
            f'<text class="tick" x="{label_w - 8}" y="{y + cell / 2 + 4:.1f}" text-anchor="end">{escape(name)}</text>'
        )
        for hour in range(24):
            value = matrix[r][hour]
            level = 0.06 + 0.94 * value / peak if value else 0.0
            tip = escape(f"{name} {hour:02d}:00-{hour:02d}:59 UTC: {value}", quote=True)
            parts.append(
                f'<rect class="cell{" on" if value else ""}" style="--level:{level:.2f};--i:{r * 24 + hour}" '
                f'x="{label_w + hour * cell + gap / 2:.1f}" y="{y + gap / 2:.1f}" width="{cell - gap:.1f}" '
                f'height="{cell - gap:.1f}" rx="3" tabindex="-1" data-tip="{tip}"/>'
            )
    for hour in range(0, 24, 3):
        parts.append(
            f'<text class="tick" x="{label_w + hour * cell + cell / 2:.1f}" y="{height - 6:.0f}" '
            f'text-anchor="middle">{hour:02d}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _empty_plot(width: int, height: int, message: str) -> str:
    """An empty chart still looks like its chart: a faint grid with the reason in the middle."""
    lines = "".join(
        f'<line class="grid" x1="20" x2="{width - 20}" y1="{y:.1f}" y2="{y:.1f}"/>'
        for y in (height * f for f in (0.2, 0.4, 0.6, 0.8))
    )
    return (
        f'<svg class="chart empty-plot" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(message)}">'
        f'{lines}<text class="empty-text" x="{width / 2}" y="{height / 2 + 4}" text-anchor="middle">'
        f"{escape(message)}</text></svg>"
    )
