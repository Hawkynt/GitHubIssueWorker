"""F14.20: the model colour wheel on the Settings page, as the server renders it."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from github_issue_worker.configuration import HueSegment, ModelColorsSettings
from github_issue_worker.domain.model_name import model_creator
from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.hue_wheel import MAKERS, hue_wheel, wheel_data
from github_issue_worker.ui.model_colors import model_series


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(DemoBackend())) as test_client:
        yield test_client


def _data(html: str) -> dict[str, object]:
    found = re.search(r'<script type="application/json" id="hue-wheel-data">(.*?)</script>', html)
    assert found, "the wheel carries its data"
    loaded: dict[str, object] = json.loads(found.group(1))
    return loaded


def test_the_model_colours_section_shows_a_wheel_with_an_arc_per_maker(client: TestClient) -> None:
    html = client.get("/settings").text
    section = html[html.index("<legend>Model colours</legend>") :]
    section = section[: section.index("</fieldset>")]
    assert 'class="hue-wheel"' in section
    makers = _data(section)["makers"]
    assert isinstance(makers, list)
    assert [(m["key"], m["label"], m["hue"], m["width"]) for m in makers] == [
        ("anthropic", "Anthropic", 70, 90),
        ("openai", "OpenAI", 250, 55),
        ("google", "Google", 155, 55),
        ("other", "Other", 320, 120),
    ]
    for maker in ("anthropic", "openai", "google", "other"):
        for part in ("hue", "width"):
            assert f'name="modelColors.{maker}.{part}"' in section  # the precise fallback stays


def test_the_wheel_stays_hidden_until_its_script_runs(client: TestClient) -> None:
    html = client.get("/settings").text
    assert re.search(r'<div class="hue-wheel" hidden', html)
    assert "hue-wheel-data" in html and "pointerdown" in html and "ArrowRight" in html


def test_the_wheel_adds_no_control_to_what_the_form_submits(client: TestClient) -> None:
    html = client.get("/settings").text
    wheel = html[html.index('<div class="hue-wheel"') : html.index("</div><!-- hue-wheel -->")]
    assert re.search(r"<(?:input|select|textarea)\b", wheel) is None
    assert "<button" not in wheel
    assert 'm.button.type = "button"' in html  # the maker buttons the script adds never submit


def test_the_wheel_follows_the_configured_segments() -> None:
    colors = ModelColorsSettings(anthropic=HueSegment(hue=10, width=200))
    makers = _data(hue_wheel(colors))["makers"]
    assert isinstance(makers, list)
    assert (makers[0]["hue"], makers[0]["width"]) == (10, 200)


@pytest.mark.parametrize("maker", MAKERS)
def test_the_preview_shows_sample_models_of_that_maker_in_their_chart_colours(maker: str) -> None:
    colors = ModelColorsSettings(google=HueSegment(hue=200, width=100))
    entry = next(m for m in wheel_data(colors)["makers"] if m["key"] == maker)
    names = [sample["name"] for sample in entry["samples"]]
    assert len(names) >= 3
    expected = model_series(names, colors)
    assert {sample["name"]: sample["color"] for sample in entry["samples"]} == expected
    for name in names:
        creator = model_creator(name)
        assert creator == maker if maker != "other" else creator not in MAKERS


def test_anthropic_samples_are_its_model_names() -> None:
    entry = wheel_data(ModelColorsSettings())["makers"][0]
    names = " ".join(sample["name"] for sample in entry["samples"])
    for family in ("Opus", "Sonnet", "Haiku", "Fable"):
        assert family in names


def test_the_data_cannot_close_its_script_element() -> None:
    assert (
        "</"
        not in hue_wheel(ModelColorsSettings())
        .split('id="hue-wheel-data">')[1]
        .split("</script>")[0]
    )
