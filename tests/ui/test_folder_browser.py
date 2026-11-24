"""F14.4 in a real browser: pick a ClaudeCodePortable folder and see what its profiles offer."""

from __future__ import annotations

import re
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
    opened.goto(server.url + "/workers/new")
    yield opened
    opened.close()


def test_browsing_to_a_portable_folder_and_using_it_lists_its_profiles(page: Page) -> None:
    panel = page.locator('[aria-label="ClaudeCodePortable folder"]')
    panel.get_by_role("button", name="Browse…").click()
    browser_box = panel.locator(".browser")
    expect(browser_box).to_be_visible()
    expect(browser_box.locator("p").first).to_have_text("/home/me")

    browser_box.get_by_role("button", name="Agents", exact=True).click()
    expect(browser_box.locator("p").first).to_have_text("/home/me/Agents")
    browser_box.get_by_role("button", name="ClaudePortable", exact=True).click()
    expect(browser_box.locator("p").first).to_contain_text("a ClaudeCodePortable folder")

    browser_box.get_by_role("button", name="Use this folder").click()
    expect(page).to_have_url(re.compile(r"root=%2Fhome%2Fme%2FAgents%2FClaudePortable"))
    rows = page.locator('[aria-label="ClaudeCodePortable folder"] tbody tr')
    expect(rows).to_have_count(4)
    expect(rows.filter(has_text="ChatGPTCodex")).to_contain_text("codex-cli 0.169.0")
    expect(rows.filter(has_text="GoogleGemini").get_by_role("button", name="Add")).to_be_disabled()


def test_going_up_from_a_folder_shows_the_one_above(page: Page) -> None:
    page.fill('[aria-label="ClaudeCodePortable folder"] input[name="root"]', "/home/me/Agents")
    panel = page.locator('[aria-label="ClaudeCodePortable folder"]')
    panel.get_by_role("button", name="Browse…").click()
    box = panel.locator(".browser")
    expect(box.locator("p").first).to_have_text("/home/me/Agents")
    box.get_by_role("button", name="..").click()
    expect(box.locator("p").first).to_have_text("/home/me")
