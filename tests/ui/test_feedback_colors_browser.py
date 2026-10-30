"""F14.18: the demo's wait and conflict colours in a real browser."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from playwright.sync_api import Browser, expect, sync_playwright

from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.server import UiServer, free_port

pytestmark = pytest.mark.os_integration


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_demo_wait_and_conflict_colors_are_readable_in_both_schemes(
    browser: Browser, scheme: str
) -> None:
    backend = DemoBackend()
    server = UiServer(backend, "127.0.0.1", free_port())
    assert server.start()
    page = browser.new_page(color_scheme=scheme, reduced_motion="reduce")  # type: ignore[arg-type]
    try:
        page.goto(server.url + "/")
        feedback = page.locator(".flag.feedback")
        assert feedback.inner_text() == "waiting for your answer"
        expect(feedback).to_have_css("color", "rgb(224, 164, 74)")
        page.goto(server.url + "/workers")
        limited = page.locator('[aria-label="antigravity"].lane')
        expect(limited).to_have_css("background-color", "rgb(37, 43, 52)")
        assert (
            "Token allowance reached" in limited.inner_text() and "resets" in limited.inner_text()
        )
        expect(limited.locator(".flag.limited").first).to_have_css("color", "rgb(180, 188, 199)")
        conflict = page.locator('[aria-label="codex"] .track .conflict')
        assert conflict.inner_text() == "merge"
        assert conflict.get_attribute("title") == "Merge conflict"
        expect(conflict).to_have_css("border-top-color", "rgb(224, 122, 107)")
        expect(page.locator('[aria-label="claude"] .track .now').first).to_have_css(
            "border-top-color", "rgb(111, 179, 232)"
        )
        # Repository identity is independent of wait/conflict styling.
        expect(page.locator('[aria-label="codex"] .square:not(.free)')).not_to_have_css(
            "background-color", "rgb(224, 122, 107)"
        )
    finally:
        page.close()
        server.stop()


def test_live_fragment_replaces_waiting_box_and_conflict_styles(
    browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = DemoBackend()
    shown = backend.snapshot()
    shown = replace(
        shown, workers=(replace(shown.workers[0], state="limited", merge_conflict=True),)
    )
    monkeypatch.setattr(backend, "snapshot", lambda: shown)
    server = UiServer(backend, "127.0.0.1", free_port())
    assert server.start()
    page = browser.new_page(reduced_motion="reduce")
    try:
        page.goto(server.url + "/workers")
        assert page.locator(".lane.waiting .track .conflict").count() == 1
        shown = replace(
            shown, workers=(replace(shown.workers[0], state="working", merge_conflict=False),)
        )
        page.evaluate("""async () => {
          const response = await fetch('/workers?fragment=1');
          document.querySelector('#live').innerHTML = await response.text();
        }""")
        assert page.locator('[aria-label="claude"].lane.waiting').count() == 0
        assert page.locator(".track .conflict").count() == 0
        assert page.locator('[aria-label="claude"] .track .now').first.inner_text() == "verify"
    finally:
        page.close()
        server.stop()


def test_current_conflict_and_wait_chips_do_not_pulse_back_to_active_color(
    browser: Browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = DemoBackend()
    snapshot = backend.snapshot()
    shown = replace(
        snapshot, workers=(replace(snapshot.workers[0], step="merge", merge_conflict=True),)
    )
    monkeypatch.setattr(backend, "snapshot", lambda: shown)
    server = UiServer(backend, "127.0.0.1", free_port())
    assert server.start()
    page = browser.new_page(reduced_motion="no-preference")
    try:
        page.goto(server.url + "/workers")
        expect(page.locator('[aria-label="claude"] .track .conflict')).to_have_css(
            "animation-name", "none"
        )
        expect(page.locator(".lane.remote .run.waiting .track .now")).to_have_css(
            "animation-name", "none"
        )
    finally:
        page.close()
        server.stop()
