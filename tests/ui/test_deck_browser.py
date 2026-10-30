"""F14.15 in a real browser: one dark deck on every page, still under reduced motion, phone-sized."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright

from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.server import UiServer, free_port

pytestmark = pytest.mark.os_integration  # needs a browser installed on the host

PAGES = (
    "/",
    "/workers",
    "/repositories",
    "/fleet",
    "/settings",
    "/workers/new",
    "/workers/claude",
    "/workers/claude/log",
)

OVERFLOWING = """() => {
  const fits = (box) => box.left >= -1 && box.right <= innerWidth + 1;
  const clipped = (node) => {
    for (let up = node.parentElement; up && up !== document.body; up = up.parentElement) {
      const style = getComputedStyle(up);
      if (["auto", "scroll", "hidden"].includes(style.overflowX)) return fits(up.getBoundingClientRect());
    }
    return false;
  };
  return [...document.querySelectorAll("body *")]
    .filter((node) => node.getClientRects().length && !fits(node.getBoundingClientRect()))
    .filter((node) => !clipped(node))
    .map((node) => node.tagName + "." + node.className);
}"""


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


@pytest.mark.parametrize("path", PAGES)
def test_every_page_is_dark_even_when_the_system_prefers_light(
    browser: Browser, server: UiServer, path: str
) -> None:
    page = browser.new_page(color_scheme="light")
    page.goto(server.url + path)
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert background == "rgb(6, 11, 20)", path
    page.close()


@pytest.mark.parametrize("path", PAGES)
def test_nothing_moves_under_reduced_motion(browser: Browser, server: UiServer, path: str) -> None:
    page = browser.new_page(reduced_motion="reduce")
    page.goto(server.url + path)
    page.wait_for_load_state("load")
    running = page.evaluate("document.getAnimations().map(a => a.animationName || a.id)")
    assert running == [], path
    page.close()


@pytest.mark.parametrize("path", PAGES)
def test_every_page_fits_a_phone(browser: Browser, server: UiServer, path: str) -> None:
    page = browser.new_page(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
    page.goto(server.url + path)
    assert page.evaluate(OVERFLOWING) == [], path
    page.close()


@pytest.mark.parametrize("path", ["/", "/fleet", "/workers"])
def test_a_live_refresh_does_not_replay_the_boot(
    browser: Browser, server: UiServer, path: str
) -> None:
    page = browser.new_page(reduced_motion="no-preference")
    page.add_init_script(
        "window.booted = [];"
        "document.addEventListener('animationstart', (event) => {"
        "  if (event.animationName === 'materialise') window.booted.push(event.target);"
        "});"
    )
    page.goto(server.url + path)
    page.evaluate("window.first = document.querySelector('#live .panel, #live .readout')")
    page.wait_for_function("!document.body.classList.contains('boot')", timeout=10_000)
    # The stream's first event arrives during the boot; its refresh swaps the live part after it.
    page.wait_for_function("!window.first.isConnected", timeout=10_000)
    assert page.evaluate("document.querySelectorAll('#live .panel').length") > 0
    assert page.evaluate(
        "window.booted.every(node => !document.getElementById('live').contains(node) || !node.isConnected)"
    )
    page.close()
