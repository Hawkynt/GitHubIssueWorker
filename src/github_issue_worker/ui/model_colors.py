"""The colour a model wears (F14.19): its maker's hue segment, its name's place in it, its shade.

Every model of one maker is drawn inside that maker's segment of the colour wheel, and inside the
segment every model *name* takes its own place — a hue and a lightness step together, because a hue
alone cannot separate colours that sit 60 degrees apart. A version or quantisation of a name keeps
that place and only changes how strong the colour is, so ``Claude Opus 5`` and ``Claude Opus 5.5``
stay one hue and remain two series in the charts. Colours are written as ``oklch()``, which is the
space the segments are configured in and the space the dataviz checks are measured in.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from github_issue_worker.configuration import HueSegment, ModelColorsSettings
from github_issue_worker.domain.model_name import model_creator, model_family

PALETTE = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
)
"""The fixed colours of a split that is not by model: by first appearance, never cycled."""

OTHER = "Other"
GREY = "oklch(0.55 0.05 260)"
"""The folded ninth series, and a model nobody knows."""

NAMED_MAKERS = frozenset(name for name in ModelColorsSettings.model_fields if name != "other")
"""The makers with a segment of their own; every other maker keeps a part of ``other``."""

AGENT_MAKERS = {"claude": "anthropic", "codex": "openai", "antigravity": "google"}
"""The maker an agent kind serves, for a lane whose worker has not reported a model yet."""

_SUBSEGMENT = 50
"""How much of the ``other`` segment one unlisted maker keeps for itself."""

_HUES = 4
"""Hue places a maker's segment is cut into, one per model *family*. They sit inside the segment
with a margin at both ends, so two makers never meet at a boundary."""

_LIGHTNESS = (0.63, 0.49, 0.565)
"""The shades a maker's series take in turn, spread over the whole lightness band a categorical
palette may use. Inside one hue segment the lightness, not the hue, is what still separates two
series: a 90-degree segment in sRGB holds no more than about four colours fifteen delta-E apart,
so the shade carries the version and the legend, tooltip and ledger name the model besides."""

_CHROMA = 0.18
"""How strong every model colour is: well over the chroma floor, low enough to stay in sRGB."""


def _unit(seed: str, key: str) -> float:
    """A stable position in ``[0, 1)`` for ``key``: the same name always lands in the same place."""
    return (zlib.crc32(key.encode(), zlib.crc32(seed.encode())) % 100_000) / 100_000


def _slot(seed: str, key: str, count: int) -> int:
    """One of ``count`` places, fixed for ``key``."""
    return int(_unit(seed, key) * count) % count


def _segment(creator: str, colors: ModelColorsSettings) -> HueSegment:
    """The maker's own segment, or a fixed part of ``other`` for a maker nobody named."""
    if creator in NAMED_MAKERS:
        segment: HueSegment = getattr(colors, creator)
        return segment
    region = colors.other
    width = min(region.width, _SUBSEGMENT)
    reach = max(region.width - width, 0)
    centre = region.hue + reach * (maker_place(creator) - 0.5)
    return HueSegment(hue=int(centre) % 360, width=width)


def _color(segment: HueSegment, family_place: int, series_place: int) -> str:
    """The colour of one series: its family's hue in the segment, its own lightness and strength."""
    hue = (segment.hue + segment.width * ((family_place % _HUES + 0.5) / _HUES - 0.5)) % 360
    lightness = _LIGHTNESS[series_place % len(_LIGHTNESS)]
    return f"oklch({lightness} {_CHROMA} {hue:.0f})"


def model_color(name: str, colors: ModelColorsSettings) -> str:
    """The colour of a model seen on its own: the first shade of its family's hue."""
    if name == OTHER or not name.strip():
        return GREY
    creator = model_creator(name)
    return _color(_segment(creator, colors), _slot("hue", model_family(name), _HUES), 0)


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one model name lands: its maker, its family's hue place, its shade at that hue."""

    name: str
    creator: str
    place: int
    shade: int


def placements(names: Iterable[str]) -> list[Placement]:
    """Each model name's place, in order of first appearance; ``Other`` and blanks are left out."""
    seen: dict[tuple[str, int], list[str]] = {}
    placed: list[Placement] = []
    for name in dict.fromkeys(names):
        if name == OTHER or not name.strip():
            continue
        creator = model_creator(name)
        place = _slot("hue", model_family(name), _HUES)
        at_hue = seen.setdefault((creator, place), [])
        at_hue.append(name)
        placed.append(Placement(name, creator, place, len(at_hue) - 1))
    return placed


def model_series(names: Iterable[str], colors: ModelColorsSettings) -> dict[str, str]:
    """Colour per model name, in order of first appearance.

    The hue of a series is its *family's* place in the maker's segment, fixed by the family's name,
    so a version change keeps it and a new model never repaints an old one. Series then take the
    lightness steps in turn *within one hue*, so ``Claude Opus 5`` and ``Claude Opus 5.5`` are one
    hue in two shades and two names of one maker never land on one colour.
    """
    ordered = list(dict.fromkeys(names))
    placed = {p.name: p for p in placements(ordered)}
    return {
        name: _color(_segment(p.creator, colors), p.place, p.shade)
        if (p := placed.get(name)) is not None
        else GREY
        for name in ordered
    }


def maker_place(creator: str) -> float:
    """Where in the ``other`` segment an unlisted maker keeps its own part, from 0 to 1."""
    return _unit("maker", creator)


WHEEL = {"hues": _HUES, "lightness": _LIGHTNESS, "chroma": _CHROMA, "subsegment": _SUBSEGMENT}
"""The constants a colour is made of, for a page that redraws model colours as a segment changes."""


def entity_series(names: Iterable[str]) -> dict[str, str]:
    """The fixed palette by first appearance; a ninth entity shares ``Other``'s grey."""
    seen = dict.fromkeys(names)
    return {
        name: PALETTE[index] if index < len(PALETTE) else GREY for index, name in enumerate(seen)
    }


def lane_color(
    model: str | None, agent_kind: str, series: Mapping[str, str], colors: ModelColorsSettings
) -> str:
    """A crew lane: its model's colour, else the default hue of its agent's maker segment."""
    if model:
        known = series.get(model)
        if known is not None:
            return known
        return model_color(model, colors)
    return f"oklch(0.55 0.13 {_segment(AGENT_MAKERS.get(agent_kind, ''), colors).hue})"
