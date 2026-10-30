"""F14.15: every page wears the cockpit's deck, its sections as panels, with readouts on top."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.pages import STYLE

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


@pytest.fixture
def backend() -> DemoBackend:
    return DemoBackend()


@pytest.fixture
def client(backend: DemoBackend) -> TestClient:
    return TestClient(create_app(backend))


def panels(html: str) -> list[str]:
    """Every panel of the page, its markup included; panels do not nest."""
    found = re.finditer(r'<(section|fieldset) class="panel[^"]*"[^>]*>.*?</\1>', html, re.S)
    return [match.group(0) for match in found]


def readouts(html: str) -> dict[str, int]:
    found = re.findall(r"<div class=readout[^>]*><span>([^<]+)</span><b data-count=(\d+)>", html)
    return {label: int(value) for label, value in found}


@pytest.mark.parametrize("path", PAGES)
def test_every_page_wears_the_deck(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert "<body class='deck boot'>" in html, path


def test_the_issues_page_lists_its_issues_in_a_panel(client: TestClient) -> None:
    (table,) = [p for p in panels(client.get("/").text) if "<table" in p]
    assert "<h2>" in table and "class=sub" in table
    assert "Retry uploads after a gateway timeout" in table


def test_the_fleet_page_shows_each_repositorys_claims_in_a_panel(
    client: TestClient, backend: DemoBackend
) -> None:
    html = client.get("/fleet").text
    claimed = {claim.repository for claim in backend.snapshot().claims}
    for repository in claimed:
        (own,) = [p for p in panels(html) if f"<h2>{repository}</h2>" in p]
        assert "<table" in own and "class=sub" in own


def test_the_repositories_page_shows_each_repository_in_a_panel(
    client: TestClient, backend: DemoBackend
) -> None:
    html = client.get("/repositories").text
    for repository in backend.snapshot().repositories:
        (own,) = [p for p in panels(html) if f">{repository.name}</a></h2>" in p]
        assert "Overrides" in own and "Stop watching" in own and "class=sub" in own
    assert any("Watch repository" in p for p in panels(html))


def test_the_issues_readouts_agree_with_its_rows(client: TestClient, backend: DemoBackend) -> None:
    html = client.get("/").text
    issues = backend.snapshot().issues
    rows = len(re.findall(r"<tr><td>", html))
    assert rows == len(issues)
    assert readouts(html) == {
        "Open": rows,
        "Worked here": sum(1 for issue in issues if issue.local),
        "Worked elsewhere": sum(1 for issue in issues if issue.worker and not issue.local),
        "Waiting": sum(1 for issue in issues if not issue.worker),
    }
    assert readouts(html)["Worked here"] == html.count("this machine</span>")


def test_the_repositories_readouts_agree_with_its_panels(
    client: TestClient, backend: DemoBackend
) -> None:
    html = client.get("/repositories").text
    snapshot = backend.snapshot()
    assert readouts(html) == {
        "Watched": len(snapshot.repositories),
        "Polled": sum(1 for r in snapshot.repositories if r.watching_since),
        "Issues in progress": sum(sum(r.counts.values()) for r in snapshot.repositories),
        "Claimed here": sum(1 for claim in snapshot.claims if claim.mine),
    }
    assert readouts(html)["Watched"] == html.count("Stop watching</button>")


def test_the_fleet_readouts_agree_with_its_rows(client: TestClient, backend: DemoBackend) -> None:
    html = client.get("/fleet").text
    claims = backend.snapshot().claims
    assert readouts(html) == {
        "Claims": html.count("<summary>Release</summary>"),
        "Held here": html.count("this manager</span>"),
        "Other managers": len({c.holder.rpartition("/")[0] for c in claims if not c.mine}),
        "Paused": html.count("paused, resumes in"),
    }
    assert readouts(html)["Claims"] == len(claims)


def test_the_readouts_refresh_with_the_live_fragment(client: TestClient) -> None:
    for path in ("/", "/fleet"):
        assert readouts(client.get(path + "?fragment=1").text), path


def test_every_settings_section_is_a_panel(client: TestClient) -> None:
    html = client.get("/settings").text
    sections = re.findall(r'<fieldset class="panel[^"]*"><legend>([^<]+)</legend>', html)
    assert sections == [
        "This machine",
        "Polling and trust",
        "This page",
        "Fleet",
        "Defaults for every repository",
        "Accounts",
    ]
    assert "<fieldset>" not in html


@pytest.mark.parametrize("path", ["/workers/new", "/workers/claude"])
def test_the_worker_form_is_made_of_panels(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert re.findall(r'<fieldset class="panel[^"]*"><legend>([^<]+)</legend>', html) == [
        "Agent",
        "Installation",
    ]
    assert "<fieldset>" not in html


def test_the_log_is_a_panel(client: TestClient) -> None:
    (log,) = [p for p in panels(client.get("/workers/claude/log").text) if "class=log" in p]
    assert "<h2>" in log


def _deck_colour(name: str) -> str:
    deck = STYLE[STYLE.index("body.deck {") :]
    found = re.search(rf"{name}: (#[0-9a-f]+)", deck[: deck.index("}")])
    assert found is not None, name
    return found.group(1)


def test_the_deck_has_change_hints_of_its_own() -> None:
    # F14.12 on the deck: a value not yet saved stands out from inputs, errors and notices.
    changed = _deck_colour("--changed")
    others = {_deck_colour(n) for n in ("--sheet", "--brick-soft", "--amber-soft", "--steel-soft")}
    assert changed not in others
    assert _deck_colour("--changed-rule") != _deck_colour("--rule")
