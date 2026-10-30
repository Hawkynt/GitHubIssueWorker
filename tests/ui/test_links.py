"""F14.17: a link that leaves the UI opens in a new tab; the UI's own pages open in the same tab."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend

PAGES = (
    "/",
    "/?fragment=1",
    "/workers",
    "/workers?fragment=1",
    "/repositories",
    "/fleet",
    "/fleet?fragment=1",
    "/settings",
    "/workers/new",
    "/workers/claude",
    "/workers/claude/log",
    "/workers/claude/log?fragment=1",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(DemoBackend()))


def links(html: str) -> list[tuple[str, str]]:
    """Every link of the page as its href and its whole opening tag."""
    found = []
    for tag in re.findall(r"<a\b[^>]*>", html):
        href = re.search(r'href="([^"]*)"|href=([^\s>]+)', tag)
        if href:
            found.append((href.group(1) or href.group(2), tag))
    return found


def outbound(html: str) -> list[str]:
    return [tag for href, tag in links(html) if re.match(r"https?://", href)]


def opens_in_a_new_tab(tag: str) -> bool:
    rel = re.search(r'rel="([^"]*)"', tag)
    return (
        'target="_blank"' in tag
        and rel is not None
        and {"noopener", "noreferrer"} <= set(rel.group(1).split())
    )


@pytest.mark.parametrize("path", PAGES)
def test_every_link_that_leaves_the_ui_opens_in_a_new_tab(client: TestClient, path: str) -> None:
    for tag in outbound(client.get(path).text):
        assert opens_in_a_new_tab(tag), (path, tag)


@pytest.mark.parametrize("path", PAGES)
def test_no_link_to_the_uis_own_pages_opens_in_a_new_tab(client: TestClient, path: str) -> None:
    for href, tag in links(client.get(path).text):
        if not re.match(r"https?://", href):
            assert "_blank" not in tag, (path, tag)


def test_the_issues_page_opens_an_issue_in_a_new_tab(client: TestClient) -> None:
    tags = [t for t in outbound(client.get("/").text) if "/issues/" in t]
    assert tags and all(opens_in_a_new_tab(t) for t in tags)


def test_the_cockpit_opens_a_local_workers_issue_in_a_new_tab(client: TestClient) -> None:
    lanes = client.get("/workers").text
    tags = [t for t in outbound(lanes) if "/issues/" in t]
    assert tags and all(opens_in_a_new_tab(t) for t in tags)


def test_the_cockpit_opens_a_remote_claims_issue_and_branch_in_a_new_tab(
    client: TestClient,
) -> None:
    html = client.get("/workers").text
    remote = html[html.index("lane remote") :]
    tags = [t for t in outbound(remote) if "/tree/" in t or "/issues/" in t]
    assert any("/tree/" in t for t in tags)
    assert tags and all(opens_in_a_new_tab(t) for t in tags)


def test_the_repositories_page_opens_a_repository_in_a_new_tab(client: TestClient) -> None:
    tags = [
        t
        for t in outbound(client.get("/repositories").text)
        if re.search(r'href="https://github\.com/[^/"]+/[^/"]+"', t)
    ]
    assert tags and all(opens_in_a_new_tab(t) for t in tags)


def test_the_fleet_page_opens_a_claimed_issue_in_a_new_tab(client: TestClient) -> None:
    tags = [t for t in outbound(client.get("/fleet").text) if "/issues/" in t]
    assert tags and all(opens_in_a_new_tab(t) for t in tags)
