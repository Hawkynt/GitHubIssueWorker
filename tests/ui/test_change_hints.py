"""F14.12 in a real browser: the Settings page marks every value that is not yet saved."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

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


def _settings(browser: Browser, server: UiServer, scheme: str = "light") -> Page:
    page = browser.new_page(color_scheme=scheme)  # type: ignore[arg-type]
    page.goto(server.url + "/settings")
    return page


def _marked(page: Page) -> list[str]:
    return page.eval_on_selector_all(
        "form [name].changed", "controls => controls.map(control => control.name)"
    )


def _background(page: Page, selector: str) -> str:
    return page.eval_on_selector(selector, "e => getComputedStyle(e).backgroundColor")


def test_editing_a_value_marks_only_that_value(browser: Browser, server: UiServer) -> None:
    page = _settings(browser, server)
    assert _marked(page) == []
    page.fill('[name="pollInterval"]', "00:02:30")
    assert _marked(page) == ["pollInterval"]


def test_editing_a_value_back_clears_its_mark(browser: Browser, server: UiServer) -> None:
    page = _settings(browser, server)
    page.fill('[name="pollInterval"]', "00:02:30")
    page.fill('[name="pollInterval"]', "00:01:00")
    assert _marked(page) == []


def test_checkboxes_and_choices_are_marked_like_text(browser: Browser, server: UiServer) -> None:
    page = _settings(browser, server)
    page.click('[name="fleet.localHandOff"]')
    current = page.eval_on_selector('[name="defaults.mergeMethod"]', "e => e.value")
    other = page.eval_on_selector(
        '[name="defaults.mergeMethod"]',
        "(e, current) => [...e.options].map(o => o.value).find(v => v !== current)",
        current,
    )
    page.select_option('[name="defaults.mergeMethod"]', other)
    assert sorted(_marked(page)) == ["defaults.mergeMethod", "fleet.localHandOff"]
    page.click('[name="fleet.localHandOff"]')
    assert _marked(page) == ["defaults.mergeMethod"]


def test_typing_a_token_marks_it_and_emptying_it_clears_it(
    browser: Browser, server: UiServer
) -> None:
    page = _settings(browser, server)
    page.fill('[name="ui.token"]', "s3cret-value")
    assert _marked(page) == ["ui.token"]
    page.fill('[name="ui.token"]', "")
    assert _marked(page) == []


def test_edited_accounts_and_a_new_account_are_marked(browser: Browser, server: UiServer) -> None:
    page = _settings(browser, server)
    page.fill('[name="accounts[3].maxConcurrentRuns"]', "2")
    page.click('[name="accounts[0].remove"]')
    page.fill('[name="accounts[4].name"]', "spare")
    assert sorted(_marked(page)) == [
        "accounts[0].remove",
        "accounts[3].maxConcurrentRuns",
        "accounts[4].name",
    ]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_a_marked_value_has_a_background_of_its_own(
    browser: Browser, server: UiServer, scheme: str
) -> None:
    page = _settings(browser, server, scheme)
    unchanged = _background(page, '[name="ui.bind"]')
    page.fill('[name="pollInterval"]', "00:02:30")
    marked = _background(page, '[name="pollInterval"]')
    others = page.evaluate(
        """() => ["--brick-soft", "--steel-soft", "--amber-soft"].map(name => {
            const probe = document.createElement("div");
            probe.style.background = `var(${name})`;
            document.body.append(probe);
            const colour = getComputedStyle(probe).backgroundColor;
            probe.remove();
            return colour;
        })"""
    )
    assert marked != unchanged
    assert marked not in others
