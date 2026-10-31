"""F13.7: the Cockpit shows each account's state, reset time and the issues waiting on it."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from github_issue_worker.application.accounts import AccountUsage
from github_issue_worker.ui import pages
from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.model import Snapshot

NOW = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)


def panel(html: str) -> str:
    return html.split('aria-label="Accounts"', 1)[1].split("</section>", 1)[0]


def rows(html: str) -> dict[str, str]:
    found = re.findall(r'<tr data-account="([^"]+)">(.*?)</tr>', panel(html), re.S)
    return dict(found)


def snapshot(*accounts: AccountUsage) -> Snapshot:
    return Snapshot("me@host", NOW, (), (), (), accounts=accounts)


def test_a_limited_account_shows_its_state_live_reset_and_links_to_its_waiting_issues() -> None:
    limited = AccountUsage(
        "anthropic-max",
        datetime(2026, 11, 6, 10, 0, tzinfo=UTC),
        (("owner/repo", 219), ("owner/repo", 230)),
    )
    html = pages.cockpit_fragment(snapshot(limited, AccountUsage("openai-plus")), [])
    row = rows(html)["anthropic-max"]
    assert "limited" in row.lower()
    assert "2026-11-06 10:00 UTC" in row
    assert 'data-until="2026-11-06T10:00:00+00:00">6d 23h<' in row  # counts down live
    assert 'href="https://github.com/owner/repo/issues/219"' in row
    assert 'href="https://github.com/owner/repo/issues/230"' in row
    assert "owner/repo#219" in row


def test_available_accounts_show_no_reset_and_no_waiting_issues() -> None:
    html = pages.cockpit_fragment(
        snapshot(AccountUsage("anthropic-max"), AccountUsage("openai-plus")), []
    )
    found = rows(html)
    assert list(found) == ["anthropic-max", "openai-plus"]
    for row in found.values():
        assert "available" in row
        assert "<time" not in row and "github.com" not in row


def test_without_accounts_the_panel_says_where_to_add_one() -> None:
    html = pages.cockpit_fragment(snapshot(), [])
    assert 'href="/settings"' in panel(html)


def test_the_demo_shows_a_limited_account_with_a_waiting_issue_and_an_available_one() -> None:
    backend = DemoBackend()
    accounts = {account.name: account for account in backend.snapshot().accounts}
    assert any(a.state == "limited" and a.waiting for a in accounts.values())
    assert any(a.state == "available" for a in accounts.values())
    html = TestClient(create_app(backend)).get("/workers").text
    found = rows(html)
    assert set(found) == set(accounts)
    limited = next(a for a in accounts.values() if a.waiting)
    repository, issue = limited.waiting[0]
    assert f'href="https://github.com/{repository}/issues/{issue}"' in found[limited.name]


def test_the_demo_lists_the_issue_its_limited_worker_paused_and_not_the_remote_one() -> None:
    accounts = {account.name: account for account in DemoBackend().snapshot().accounts}
    waiting = [issue for account in accounts.values() for issue in account.waiting]
    assert ("acme/telemetry-gateway", 219) not in waiting  # paused by another manager


def test_the_live_backend_builds_each_account_from_its_limit_and_its_paused_claims() -> None:
    from types import SimpleNamespace

    from github_issue_worker.application.manager import WorkerSlot
    from github_issue_worker.configuration import validate
    from github_issue_worker.ui.live import RuntimeBackend
    from github_issue_worker.ui.model import ClaimView

    reset = NOW + timedelta(days=6, hours=23)
    limits = {"anthropic-max": reset, "openai-plus": NOW - timedelta(minutes=1)}
    config = validate({"accounts": {"anthropic-max": {}, "openai-plus": {}, "spare": {}}})
    backend = object.__new__(RuntimeBackend)
    backend._runtime = SimpleNamespace(
        manager_id="m", config=config, state=SimpleNamespace(limited_until=limits.get)
    )
    backend._manager = SimpleNamespace(
        workers=[
            WorkerSlot("claude", "claude", "anthropic-max"),
            WorkerSlot("claude.2", "claude", "anthropic-max", worker="claude"),
            WorkerSlot("codex", "codex", "openai-plus"),
        ]
    )

    def held(issue: int, holder: str, state: str, mine: bool = True) -> ClaimView:
        return ClaimView("o/r", issue, holder, mine, state, "implement", NOW, reset, "b", reset)

    backend._claims = (
        held(219, "m/claude.2", "paused"),
        held(220, "m/codex", "working"),
        held(221, "laptop/claude.2", "paused", mine=False),
    )
    accounts = {account.name: account for account in backend._accounts(NOW)}
    assert list(accounts) == ["anthropic-max", "openai-plus", "spare"]
    assert accounts["anthropic-max"].state == "limited"
    assert accounts["anthropic-max"].limited_until == reset
    assert accounts["anthropic-max"].waiting == (("o/r", 219),)
    assert accounts["openai-plus"].state == "available"  # its limit has passed
    assert accounts["openai-plus"].waiting == ()
    assert accounts["spare"].state == "available"
