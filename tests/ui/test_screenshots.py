"""F14.7: the README's screenshots are captured by CI from the demo scenario, the Workers page first."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.server import SCREENSHOTS, capture_screenshots

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"
GENERATE = ROOT / ".github" / "workflows" / "generate.yml"
IMAGE = re.compile(r"!\[([^\]]*)\]\(docs/screenshots/([^)]+\.png)\)")


def _captured() -> set[str]:
    return {name for name, _path, _whole in SCREENSHOTS}


def test_the_hero_image_is_the_workers_page() -> None:
    hero = IMAGE.search(README.read_text(encoding="utf-8"))
    assert hero is not None
    alt, name = hero.groups()
    assert name == "workers.png"
    assert "Workers page" in alt


def test_the_readme_shows_exactly_the_captured_pages() -> None:
    shown = {name for _alt, name in IMAGE.findall(README.read_text(encoding="utf-8"))}
    assert shown == _captured()


def test_generate_validates_and_commits_every_captured_page() -> None:
    workflow = GENERATE.read_text(encoding="utf-8")
    validated = re.search(r"for file in ([\w ]+); do", workflow)
    assert validated is not None
    assert {f"{name}.png" for name in validated.group(1).split()} == _captured()
    committed = set(re.findall(r"file: docs/screenshots/(\S+\.png)", workflow))
    assert committed == _captured()


def test_generate_runs_ci_on_the_head_that_carries_the_screenshots() -> None:
    workflow = GENERATE.read_text(encoding="utf-8")
    trigger = workflow[workflow.index("  trigger-ci:") :]
    assert "needs: screenshots" in trigger
    assert "gh workflow run ci.yml" in trigger


@pytest.mark.os_integration  # needs a browser installed on the host
def test_every_page_is_captured_from_the_demo_scenario(tmp_path: Path) -> None:
    written = capture_screenshots(DemoBackend(), tmp_path)
    assert {path.name for path in written} == _captured()
    for path in written:
        assert path.stat().st_size > 10_000, path.name
