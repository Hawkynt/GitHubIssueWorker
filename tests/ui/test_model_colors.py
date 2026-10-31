"""F14.19: a model's colour comes from its maker's segment and its own name, never its version."""

from __future__ import annotations

import re

import pytest

from github_issue_worker.configuration import HueSegment, ModelColorsSettings
from github_issue_worker.ui.model_colors import (
    GREY,
    OTHER,
    entity_series,
    lane_color,
    model_color,
    model_series,
)

DEFAULTS = ModelColorsSettings()


def parts(color: str) -> tuple[float, float, int]:
    """The lightness, chroma and hue of an ``oklch()`` colour."""
    match = re.fullmatch(r"oklch\(([\d.]+) ([\d.]+) (\d+)\)", color)
    assert match, color
    return float(match.group(1)), float(match.group(2)), int(match.group(3))


def hue(color: str) -> int:
    return parts(color)[2]


def in_segment(color: str, low: int, high: int) -> bool:
    """Whether a colour's hue lies in the segment from ``low`` to ``high``, which may wrap past 360."""
    place = hue(color)
    return low <= place <= high if low <= high else place >= low or place <= high


def test_a_model_wears_the_hue_segment_of_its_maker() -> None:
    assert 35 <= hue(model_color("Claude Opus 5.5", DEFAULTS)) <= 105  # orange to yellow
    assert 227 <= hue(model_color("GPT 6.1 Sol", DEFAULTS)) <= 272  # blue
    assert 132 <= hue(model_color("Gemini 4 Argon (Medium)", DEFAULTS)) <= 177  # green


def test_an_unlisted_maker_keeps_a_fixed_segment_of_its_own() -> None:
    other = DEFAULTS.other
    low, high = (other.hue - other.width // 2) % 360, (other.hue + other.width // 2) % 360
    for name in ("Phi-4", "Granite 3.3", "Command R+", "Strata 8B"):
        assert in_segment(model_color(name, DEFAULTS), low, high), name


VENDOR_MODELS = {
    "anthropic": "Claude Opus 5.5",
    "openai": "GPT 6.1 Sol",
    "google": "Gemini 2.5 Pro",
    "meta": "Llama 4 Maverick",
    "xai": "Grok 4",
    "deepseek": "DeepSeek V3",
    "nvidia": "Nemotron Ultra",
    "alibaba": "Qwen3.8 Flash Next IQ3_S",
    "mistral": "Mistral Large 3",
    "moonshot": "Kimi K2",
    "zai": "GLM-4.6",
}


def _bounds(segment: HueSegment) -> tuple[int, int]:
    return (segment.hue - segment.width // 2) % 360, (segment.hue + segment.width // 2) % 360


@pytest.mark.parametrize(("maker", "name"), VENDOR_MODELS.items())
def test_a_named_vendor_wears_its_own_segment_and_no_other(maker: str, name: str) -> None:
    color = model_color(name, DEFAULTS)
    assert in_segment(color, *_bounds(getattr(DEFAULTS, maker))), name
    for other in type(DEFAULTS).model_fields:
        if other != maker:
            assert not in_segment(color, *_bounds(getattr(DEFAULTS, other))), (name, other)


def test_the_hue_spreads_over_the_makers_segment() -> None:
    series = model_series(
        [f"Claude {line} 5.5" for line in ("opus", "sonnet", "haiku", "fable")], DEFAULTS
    )
    hues = {hue(color) for color in series.values()}
    spread = max(hues) - min(hues)
    assert spread >= DEFAULTS.anthropic.width * 0.7  # spread over the segment, not stacked


def test_two_names_of_one_maker_never_share_a_colour() -> None:
    series = model_series(
        ["Claude Opus 5.5", "Claude Sonnet 5.5", "Claude Haiku 5.5", "Claude Fable 5.5"], DEFAULTS
    )
    assert len(set(series.values())) == 4


def test_a_name_keeps_its_hue_when_other_models_arrive() -> None:
    alone = model_series(["Claude Opus 5.5"], DEFAULTS)["Claude Opus 5.5"]
    later = model_series(["Claude Sonnet 5.5", "Claude Opus 5.5"], DEFAULTS)["Claude Opus 5.5"]
    assert hue(alone) == hue(later)


def test_a_version_change_keeps_the_hue_and_moves_only_the_shade() -> None:
    series = model_series(["Claude Opus 5", "Claude Opus 5.5"], DEFAULTS)
    older, newer = series["Claude Opus 5"], series["Claude Opus 5.5"]
    assert hue(older) == hue(newer)
    assert parts(older)[0] != parts(newer)[0]


def test_a_quantisation_keeps_the_hue_of_its_model() -> None:
    series = model_series(["Qwen3.8 Flash Next IQ3_S", "Qwen3.8 Flash Next BF16"], DEFAULTS)
    quantised, plain = series.values()
    assert hue(quantised) == hue(plain)


def test_the_segments_are_settings() -> None:
    colors = ModelColorsSettings(anthropic=HueSegment(hue=200, width=20))
    assert 190 <= hue(model_color("Claude Opus 5.5", colors)) <= 210
    assert hue(model_color("GPT 6.1 Sol", colors)) == hue(model_color("GPT 6.1 Sol", DEFAULTS))


@pytest.mark.parametrize("name", [OTHER, "", "  "])
def test_the_folded_series_and_an_unknown_model_stay_grey(name: str) -> None:
    assert model_series([name], DEFAULTS)[name] == GREY


def test_a_split_that_is_not_by_model_keeps_the_fixed_palette() -> None:
    series = entity_series(["claude", "codex", "claude", "antigravity"])
    assert list(series) == ["claude", "codex", "antigravity"]
    assert len({series["claude"], series["codex"], series["antigravity"]}) == 3
    beyond = entity_series([f"m{i}" for i in range(10)])
    assert beyond["m9"] == GREY


def test_a_lane_wears_its_model_colour_and_otherwise_its_makers_default_hue() -> None:
    series = model_series(["Claude Opus 5.5", "GPT 6.1 Sol"], DEFAULTS)
    assert lane_color("Claude Opus 5.5", "claude", series, DEFAULTS) == series["Claude Opus 5.5"]
    assert in_segment(lane_color(None, "claude", {}, DEFAULTS), *_bounds(DEFAULTS.anthropic))
    assert in_segment(lane_color(None, "codex", {}, DEFAULTS), *_bounds(DEFAULTS.openai))
    assert in_segment(lane_color(None, "opencode", {}, DEFAULTS), *_bounds(DEFAULTS.other))
