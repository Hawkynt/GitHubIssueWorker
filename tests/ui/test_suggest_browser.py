"""F14.16 in a real browser: the add box completes owners, then their repositories."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

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


@pytest.fixture
def page(browser: Browser, server: UiServer) -> Iterator[Page]:
    opened = browser.new_page(reduced_motion="reduce")
    opened.goto(server.url + "/repositories")
    yield opened
    opened.close()


def options(page: Page) -> list[str]:
    return page.locator("#repository-list [role=option]").all_text_contents()


def test_typing_part_of_an_owner_lists_the_owners(page: Page) -> None:
    page.locator("#repository").press_sequentially("ac")
    expect(page.locator("#repository-list")).to_be_visible()
    assert options(page) == ["acme/", "acme-labs/"]
    expect(page.locator("#repository")).to_have_attribute("aria-expanded", "true")


def test_picking_an_owner_by_keyboard_goes_on_to_its_repositories(page: Page) -> None:
    box = page.locator("#repository")
    box.press_sequentially("ha")
    expect(page.locator("#repository-list [role=option]")).to_have_text(["hana-ito/"])
    box.press("ArrowDown")
    expect(box).to_have_attribute("aria-activedescendant", "repository-option-0")
    box.press("Enter")
    expect(box).to_have_value("hana-ito/")
    expect(page.locator("#repository-list [role=option]")).to_have_text(
        ["hana-ito/dotfiles", "hana-ito/notes-sync"]
    )
    box.press("ArrowUp")
    box.press("Enter")
    expect(box).to_have_value("hana-ito/notes-sync")
    expect(page.locator("#repository-list")).to_be_hidden()


def test_picking_by_mouse_and_watching_the_repository(page: Page) -> None:
    box = page.locator("#repository")
    box.press_sequentially("acme/ing")
    page.locator("#repository-list [role=option]", has_text="acme/ingest-worker").click()
    expect(box).to_have_value("acme/ingest-worker")
    page.get_by_role("button", name="Watch repository").click()
    expect(page.locator(".notice")).to_contain_text("Watching acme/ingest-worker.")


def test_escape_closes_and_free_text_still_submits(page: Page) -> None:
    box = page.locator("#repository")
    box.press_sequentially("acme")
    expect(page.locator("#repository-list")).to_be_visible()
    box.press("Escape")
    expect(page.locator("#repository-list")).to_be_hidden()
    box.fill("https://github.com/acme/brand-new")
    box.press("Enter")
    expect(page.locator(".notice")).to_contain_text("Watching acme/brand-new.")
