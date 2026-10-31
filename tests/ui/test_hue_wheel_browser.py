"""F14.20: the model colour wheel driven by mouse, touch and keyboard in a real browser."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Locator, Page, expect, sync_playwright

from github_issue_worker.configuration import HueSegment, ModelColorsSettings
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.hue_wheel import SAMPLES
from github_issue_worker.ui.model_colors import model_series
from github_issue_worker.ui.server import UiServer, free_port

pytestmark = pytest.mark.os_integration

TRACK = {"anthropic": 150, "openai": 128, "google": 106, "other": 84}
"""The radius each maker's arc is drawn at, in the wheel's own units."""

SCREEN = """([angle, radius]) => {
  const svg = document.querySelector("svg.wheel");
  const point = svg.createSVGPoint();
  const a = angle * Math.PI / 180;
  point.x = 200 + radius * Math.sin(a);
  point.y = 200 - radius * Math.cos(a);
  const screen = point.matrixTransform(svg.getScreenCTM());
  return [screen.x, screen.y];
}"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


@pytest.fixture
def backend() -> DemoBackend:
    return DemoBackend()


@pytest.fixture
def server(backend: DemoBackend) -> Iterator[UiServer]:
    running = UiServer(backend, "127.0.0.1", free_port())
    assert running.start()
    yield running


def _open(browser: Browser, server: UiServer, **options: object) -> Page:
    page = browser.new_page(reduced_motion="reduce", **options)  # type: ignore[arg-type]
    page.goto(server.url + "/settings")
    page.locator("svg.wheel").scroll_into_view_if_needed()
    return page


def _point(page: Page, angle: float, radius: float) -> tuple[float, float]:
    x, y = page.evaluate(SCREEN, [angle, radius])
    return float(x), float(y)


def _handle(page: Page, maker: str, part: str) -> Locator:
    return page.locator(f'.wheel [data-maker="{maker}"] [data-part="{part}"]')


def _drag(page: Page, maker: str, start: float, end: float) -> None:
    page.mouse.move(*_point(page, start, TRACK[maker]))
    page.mouse.down()
    for step in range(1, 6):
        page.mouse.move(*_point(page, start + (end - start) * step / 5, TRACK[maker]))
    page.mouse.up()


def _field(page: Page, maker: str, part: str) -> Locator:
    return page.locator(f'[name="modelColors.{maker}.{part}"]')


def _swatches(page: Page) -> dict[str, str]:
    found: dict[str, str] = page.evaluate(
        "() => Object.fromEntries([...document.querySelectorAll('.wheel-swatches li')]"
        ".map((li) => [li.dataset.model, li.dataset.color]))"
    )
    return found


def test_the_wheel_shows_every_maker_as_a_labelled_arc(browser: Browser, server: UiServer) -> None:
    page = _open(browser, server)
    expect(page.locator(".hue-wheel")).to_be_visible()
    assert page.locator(".wheel .ring path").count() == 120
    for maker, label, hue, width in (
        ("anthropic", "Anthropic", 70, 90),
        ("openai", "OpenAI", 250, 55),
        ("google", "Google", 155, 55),
        ("other", "Other", 320, 120),
    ):
        group = page.locator(f'.wheel [data-maker="{maker}"]')
        expect(group.locator(".label")).to_have_text(label)
        assert group.locator(".band path").count() > 0
        expect(_handle(page, maker, "hue")).to_have_attribute("aria-valuenow", str(hue))
        expect(_handle(page, maker, "start")).to_have_attribute("aria-valuenow", str(width))
        expect(_handle(page, maker, "hue")).to_have_attribute("aria-label", f"{label} hue")
    page.close()


def test_dragging_the_middle_of_an_arc_moves_its_hue_live(
    browser: Browser, server: UiServer
) -> None:
    page = _open(browser, server)
    hue = _field(page, "anthropic", "hue")
    page.mouse.move(*_point(page, 70, TRACK["anthropic"]))
    page.mouse.down()
    page.mouse.move(*_point(page, 85, TRACK["anthropic"]))
    page.mouse.move(*_point(page, 100, TRACK["anthropic"]))
    expect(hue).to_have_value("100")  # live, before the button is let go
    expect(page.locator(".wheel")).to_have_class("wheel editing dragging")
    expect(page.locator('.wheel [data-maker="anthropic"]')).to_have_class("maker active")
    page.mouse.up()
    expect(_field(page, "anthropic", "width")).to_have_value("90")
    expect(_handle(page, "anthropic", "hue")).to_have_attribute("aria-valuenow", "100")
    expect(page.locator(".wheel")).to_have_class("wheel")  # the highlight ends with the drag
    _drag(page, "anthropic", 100, -20)
    expect(hue).to_have_value("340")  # wraps across 0
    page.close()


def test_dragging_an_end_of_an_arc_changes_its_width_around_the_centre(
    browser: Browser, server: UiServer
) -> None:
    page = _open(browser, server)
    _drag(page, "anthropic", 115, 130)
    expect(_field(page, "anthropic", "width")).to_have_value("120")
    expect(_field(page, "anthropic", "hue")).to_have_value("70")
    _drag(page, "anthropic", 70 - 60, 70 - 20)  # the other end, narrowing
    expect(_field(page, "anthropic", "width")).to_have_value("40")
    expect(_handle(page, "anthropic", "end")).to_have_attribute("aria-valuenow", "40")
    page.close()


def test_arrow_keys_move_the_hue_and_change_the_width(browser: Browser, server: UiServer) -> None:
    page = _open(browser, server)
    middle = page.locator('.wheel [data-maker="google"] [data-part="hue"]')
    middle.focus()
    expect(page.locator('.wheel [data-maker="google"]')).to_have_class("maker active")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Shift+ArrowUp")
    expect(_field(page, "google", "hue")).to_have_value("166")
    end = page.locator('.wheel [data-maker="google"] [data-part="end"]')
    end.focus()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Shift+ArrowDown")
    expect(_field(page, "google", "width")).to_have_value("44")
    expect(end).to_have_attribute("aria-valuenow", "44")
    _field(page, "openai", "hue").fill("359")
    page.locator('.wheel [data-maker="openai"] [data-part="hue"]').focus()
    page.keyboard.press("ArrowRight")
    expect(_field(page, "openai", "hue")).to_have_value("0")
    page.locator("h1").click()
    expect(page.locator(".wheel")).to_have_class("wheel")
    page.close()


@pytest.mark.parametrize(
    "segments",
    [
        {"anthropic": (70, 90), "openai": (250, 55), "google": (155, 55), "other": (320, 120)},
        {"anthropic": (4, 4), "openai": (359, 12), "google": (0, 360), "other": (10, 300)},
        {"anthropic": (181, 1), "openai": (2, 0), "google": (99, 179), "other": (0, 33)},
    ],
)
def test_the_preview_shows_the_colours_a_chart_would_draw(
    browser: Browser, server: UiServer, segments: dict[str, tuple[int, int]]
) -> None:
    page = _open(browser, server)
    for maker, (hue, width) in segments.items():
        _field(page, maker, "hue").fill(str(hue))
        _field(page, maker, "width").fill(str(width))
        expect(_handle(page, maker, "hue")).to_have_attribute("aria-valuenow", str(hue))
        expect(_handle(page, maker, "end")).to_have_attribute("aria-valuenow", str(width))
    colors = ModelColorsSettings(
        **{maker: HueSegment(hue=hue, width=width) for maker, (hue, width) in segments.items()}
    )
    for maker in segments:
        page.locator(f'.wheel [data-maker="{maker}"] [data-part="hue"]').focus()
        assert _swatches(page) == model_series(SAMPLES[maker], colors), maker
    page.close()


def test_a_value_out_of_range_leaves_the_wheel_where_it_was(
    browser: Browser, server: UiServer
) -> None:
    page = _open(browser, server)
    _field(page, "anthropic", "hue").fill("400")
    _field(page, "anthropic", "width").fill("12.5")
    expect(_handle(page, "anthropic", "hue")).to_have_attribute("aria-valuenow", "70")
    expect(_handle(page, "anthropic", "end")).to_have_attribute("aria-valuenow", "90")
    page.close()


def test_a_dragged_value_is_marked_unsaved_until_it_is_back(
    browser: Browser, server: UiServer
) -> None:
    page = _open(browser, server)
    hue = _field(page, "anthropic", "hue")
    expect(hue).not_to_have_class("changed")
    _drag(page, "anthropic", 70, 95)
    expect(hue).to_have_class("changed")
    expect(_field(page, "anthropic", "width")).not_to_have_class("changed")
    _drag(page, "anthropic", 95, 70)
    expect(hue).to_have_value("70")
    expect(hue).not_to_have_class("changed")
    page.close()


def test_save_stores_what_the_wheel_set(
    browser: Browser, server: UiServer, backend: DemoBackend
) -> None:
    page = _open(browser, server)
    _drag(page, "google", 155, 175)
    _drag(page, "google", 155 + 27.5 + 20, 155 + 27.5 + 30)  # the end of the moved arc
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".notice")).to_contain_text("Settings saved")
    google = backend.config().model_colors.google
    assert (google.hue, google.width) == (175, 75)
    page.close()


def test_a_dragged_width_survives_save_and_reload_with_its_hue(
    browser: Browser, server: UiServer, backend: DemoBackend
) -> None:
    default = ModelColorsSettings().anthropic
    page = _open(browser, server)
    end = default.hue + default.width / 2
    _drag(page, "anthropic", end, end + 15)
    width = default.width + 30
    expect(_field(page, "anthropic", "width")).to_have_value(str(width))
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".notice")).to_contain_text("Settings saved")
    page.reload()
    expect(_handle(page, "anthropic", "hue")).to_have_attribute("aria-valuenow", str(default.hue))
    expect(_handle(page, "anthropic", "end")).to_have_attribute("aria-valuenow", str(width))
    expect(_field(page, "anthropic", "hue")).to_have_value(str(default.hue))
    page.close()


def test_a_dragged_hue_survives_save_and_reload_with_its_width(
    browser: Browser, server: UiServer, backend: DemoBackend
) -> None:
    default = ModelColorsSettings().google
    page = _open(browser, server)
    _drag(page, "google", default.hue, default.hue + 25)
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".notice")).to_contain_text("Settings saved")
    page.reload()
    expect(_handle(page, "google", "hue")).to_have_attribute("aria-valuenow", str(default.hue + 25))
    expect(_handle(page, "google", "end")).to_have_attribute("aria-valuenow", str(default.width))
    expect(_field(page, "google", "width")).to_have_value(str(default.width))
    page.close()


def test_a_finger_drags_an_arc_without_scrolling_the_page(
    browser: Browser, server: UiServer
) -> None:
    context = browser.new_context(
        has_touch=True, is_mobile=True, viewport={"width": 390, "height": 844}
    )
    page = context.new_page()
    page.goto(server.url + "/settings")
    page.locator(".hue-wheel").scroll_into_view_if_needed()
    expect(page.locator("svg.wheel")).to_have_css("touch-action", "none")
    before = page.evaluate("scrollY")
    session = context.new_cdp_session(page)
    x, y = _point(page, 70, TRACK["anthropic"])
    session.send(
        "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]}
    )
    for angle in (75, 80, 85, 90):
        x, y = _point(page, angle, TRACK["anthropic"])
        session.send(
            "Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]}
        )
    session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    expect(_field(page, "anthropic", "hue")).to_have_value("90")
    assert page.evaluate("scrollY") == before
    context.close()


def test_the_wheel_fits_a_phone_with_targets_a_finger_can_hit(
    browser: Browser, server: UiServer
) -> None:
    page = _open(browser, server, viewport={"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    for selector in (".hue-wheel", "svg.wheel", ".wheel-swatches", ".wheel .label"):
        for box in page.evaluate(
            f"[...document.querySelectorAll('{selector}')].map((n) => {{"
            "const b = n.getBoundingClientRect(); return [b.left, b.right]; })"
        ):
            assert box[0] >= 0 and box[1] <= 375, selector
    sizes = page.evaluate(
        "[...document.querySelectorAll('.wheel .hit')].map((n) => n.getBoundingClientRect().width)"
    )
    assert len(sizes) == 12 and min(sizes) >= 24
    page.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_wheel_reads_well_in_both_schemes(
    browser: Browser, server: UiServer, scheme: str
) -> None:
    page = _open(browser, server, color_scheme=scheme)
    _handle(page, "anthropic", "hue").focus()
    # The deck is dark whatever the system prefers; the wheel's text stays light on it.
    expect(page.locator(".wheel .core-name")).to_have_css("fill", "rgb(233, 240, 248)")
    expect(page.locator(".wheel-readout")).to_have_css("color", "rgb(233, 240, 248)")
    expect(page.locator(".wheel-hint")).to_have_css("color", "rgb(140, 156, 178)")
    expect(page.locator("body")).to_have_css("background-color", "rgb(6, 11, 20)")
    expect(page.locator('.wheel [data-maker="openai"]')).to_have_css("opacity", "0.28")
    page.close()


def test_without_a_script_the_number_fields_alone_edit_the_colours(
    browser: Browser, server: UiServer, backend: DemoBackend
) -> None:
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(server.url + "/settings")
    expect(page.locator(".hue-wheel")).to_be_hidden()
    _field(page, "openai", "width").fill("80")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator(".notice")).to_contain_text("Settings saved")
    assert backend.config().model_colors.openai.width == 80
    context.close()
