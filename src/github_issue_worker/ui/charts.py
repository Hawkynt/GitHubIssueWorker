"""Charts as inline SVG, drawn on the server: no library, no network, works offline.

Mark specs follow the dataviz method: thin marks, a 2px surface gap between stacked segments,
4px rounded data ends, recessive grid and axes, a hover target larger than the mark, and colour
that follows the entity (its slot by first appearance), never its rank.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
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
        return f'<p class="chart-empty">nothing yet: the first {unit} issue lands here</p>'
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
                parts.append(f'<path class="seg s{slots.get(name, 0)}" d="{shape}"/>')
            else:
                parts.append(
                    f'<rect class="seg s{slots.get(name, 0)}" x="{x:.1f}" y="{base - segment + gap:.1f}" '
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
        return '<p class="chart-empty">nothing yet: times appear with the first merged issue</p>'
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
            f'<rect class="bar" x="{label_w}" y="{y + 6}" width="{bar:.1f}" height="14" rx="4"/>'
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
