"""Serving the UI, and capturing its pages as screenshots from the demo scenario (F14.1, F14.7)."""

from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path

import uvicorn

from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.model import Backend

log = logging.getLogger(__name__)

SCREENSHOTS = (
    ("workers.png", "/", False),
    ("repositories.png", "/repositories", False),
    ("fleet.png", "/fleet", False),
    ("settings.png", "/settings", True),
)
"""File, page, and whether the whole page is captured rather than one screen."""


class UiServer:
    """uvicorn in a background thread, so the manager's loop keeps the main thread."""

    def __init__(self, backend: Backend, host: str, port: int) -> None:
        config = uvicorn.Config(create_app(backend), host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.url = f"http://{'localhost' if host in ('127.0.0.1', '::1') else host}:{port}"

    def start(self, timeout: float = 10) -> bool:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return True
            if not self._thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def capture_screenshots(backend: Backend, directory: Path) -> list[Path]:
    """Every page of ``backend`` (the demo scenario) as a PNG in ``directory``."""
    from playwright.sync_api import sync_playwright

    directory.mkdir(parents=True, exist_ok=True)
    server = UiServer(backend, "127.0.0.1", free_port())
    if not server.start():
        raise RuntimeError("the UI did not start")
    written = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={"width": 1280, "height": 820}, color_scheme="light", locale="en-US"
            )
            for name, path, whole in SCREENSHOTS:
                page.goto(server.url + path, wait_until="networkidle")
                target = directory / name
                page.screenshot(path=str(target), full_page=whole, animations="disabled")
                written.append(target)
            browser.close()
    finally:
        server.stop()
    return written
