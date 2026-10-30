"""The cockpit in a real browser: a chart's switches redraw only that chart (F14.13)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright

from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.server import UiServer, free_port

pytestmark = pytest.mark.os_integration  # needs a browser installed on the host


@pytest.fixture(scope="module")
def server() -> Iterator[UiServer]:
    running = UiServer(DemoBackend(), "127.0.0.1", free_port())
    assert running.start()
    yield running
    running.stop()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def test_a_switch_redraws_only_its_own_chart(browser: Browser, server: UiServer) -> None:
    page = browser.new_page(reduced_motion="reduce")
    page.goto(server.url + "/workers")
    page.evaluate("window.untouched = true")  # gone if the page reloads
    page.click("#throughput .switch a:text-is('Agent')")
    page.wait_for_selector("#throughput .switch a[aria-current]:text-is('Agent')")
    legend = page.inner_text("#throughput .legend")
    assert "antigravity" in legend and "Claude Opus 5.5" not in legend
    assert page.evaluate("window.untouched") is True
    assert page.url.endswith("?by=agent&days=30&time=model")
    assert "Claude Opus 5.5" in page.inner_text("#time")  # the other chart kept its split


def test_times_tick_and_the_page_refreshes_in_the_background(
    browser: Browser, server: UiServer
) -> None:
    page = browser.new_page(reduced_motion="reduce")
    page.goto(server.url + "/workers")
    clock = page.inner_text("[data-clock]")
    page.wait_for_function(f"document.querySelector('[data-clock]').textContent !== '{clock}'")
    page.evaluate(
        "window.untouched = true; window.fetched = 0; const f = window.fetch;"
        "window.fetch = (...a) => { window.fetched++; return f(...a); }"
    )
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_function("window.fetched > 0")
    assert page.evaluate("window.untouched") is True  # refreshed in place, not reloaded


def test_buttons_answer_with_many_tabs_open(browser: Browser, server: UiServer) -> None:
    # Browsers allow six connections per host; a stream held open by every tab used them up and
    # a click on Pause or Resume never got through.
    context = browser.new_context(reduced_motion="reduce")
    for _ in range(7):
        context.new_page().goto(server.url + "/workers")
    page = context.new_page()
    page.goto(server.url + "/workers")
    with page.expect_navigation(timeout=5000):
        page.click("form[action='/workers/local/pause'] button")
    assert "local takes no new issue until resumed" in page.inner_text("main")
    with page.expect_navigation(timeout=5000):
        page.click("form[action='/workers/local/resume'] button")
    assert "local takes issues again" in page.inner_text("main")
    context.close()
