"""F14.6: the Issue page, its panels and its actions, rendered through the demo backend."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend
from github_issue_worker.ui.model import LOG_TAIL, IssueDetail

LOCAL = "/issues/acme/telemetry-gateway/214"
REMOTE = "/issues/acme/billing-cli/91"
FREE = "/issues/acme/telemetry-gateway/221"


@pytest.fixture
def backend() -> DemoBackend:
    return DemoBackend()


@pytest.fixture
def client(backend: DemoBackend) -> TestClient:
    return TestClient(create_app(backend))


def panel(html: str, label: str) -> str:
    """The markup of the panel labelled ``label``."""
    start = html.index(f'aria-label="{label}"')
    end = html.find("</section>", start)
    return html[start:end]


def test_the_issue_page_shows_the_issue_and_its_claim_in_the_deck(client: TestClient) -> None:
    # Scenario 1: title linked to GitHub in a new tab, state in words, holder, state, step,
    # branch, renewal and expiry.
    response = client.get(LOCAL)
    assert response.status_code == 200
    html = response.text
    assert "class='deck boot'" in html and 'id="live"' in html.replace("id=live", 'id="live"')
    assert (
        '<a href="https://github.com/acme/telemetry-gateway/issues/214" target="_blank" '
        'rel="noopener noreferrer">#214 Retry uploads after a gateway timeout</a>'
    ) in html
    claim = panel(html, "Claim")
    assert "worked here" in claim
    assert "claude" in claim and "this manager" in claim
    assert "working" in claim and "implement" in claim
    assert "issue/214-retry-uploads-after-a-gateway-timeout" in claim
    assert "Renewed" in claim and "Expires" in claim
    assert "2026-10-30 14:16 UTC" in claim  # renewed four minutes before the demo's now


def test_an_unknown_issue_goes_back_to_the_issues_page_with_a_notice(client: TestClient) -> None:
    for path in ("/issues/acme/telemetry-gateway/9999", "/issues/someone/else/1"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/?notice=")


def test_the_runs_panel_lists_every_run_once_oldest_first(client: TestClient) -> None:
    # Scenario 2: the claim's runs merged with the local history, each with its facts.
    runs = panel(client.get(LOCAL).text, "Runs")
    rows = re.findall(r"<tr><td[^>]*>(.*?)</tr>", runs)
    assert len(rows) == 3
    assert "understand" in rows[0] and "laptop-01ab" in rows[0] and "done" in rows[0]
    assert "GPT 6.1 Sol" in rows[1] and "needs-input" in rows[1]
    assert "Claude Opus 5.5" in rows[2] and "office-pc-7f3a" in rows[2] and "failed" in rows[2]
    assert "2026-10-29 09:12" in rows[0]  # started
    assert "18m" in rows[0]  # how long it took


def test_the_runs_panel_merges_claim_and_history_without_duplicates(backend: DemoBackend) -> None:
    detail = backend.issue("acme/telemetry-gateway", 214)
    assert detail is not None
    keys = [(run.manager, run.worker, run.started) for run in detail.runs]
    assert len(keys) == len(set(keys))
    assert [run.started for run in detail.runs] == sorted(run.started for run in detail.runs)


def test_the_journal_panel_shows_the_worktrees_journal_escaped(
    client: TestClient, backend: DemoBackend
) -> None:
    # Scenario 3: the journal of this machine's worktree, as text and never as markup.
    journal = panel(client.get(LOCAL).text, "Journal")
    assert "Retries back off 1, 2 and 4 seconds" in journal
    assert "&lt;5 retries&gt;" in journal and "<5 retries>" not in journal
    remote = panel(client.get(REMOTE).text, "Journal")
    assert "not available on this machine" in remote


def test_the_outcome_panel_shows_the_outcome_or_says_there_is_none(client: TestClient) -> None:
    # Scenario 4: outcome, summary, scenarios and questions with their options.
    outcome = panel(client.get("/issues/acme/telemetry-gateway/221").text, "Outcome")
    assert "needs-input" in outcome
    assert "The gateway URL can come from" in outcome
    assert "GATEWAY_URL only" in outcome and "Recommended" in outcome
    local = panel(client.get(LOCAL).text, "Outcome")
    assert "no outcome yet" in local


def test_a_missing_or_malformed_outcome_still_renders_the_page(backend: DemoBackend) -> None:
    detail = backend.issue("acme/telemetry-gateway", 214)
    assert detail is not None and detail.outcome is None
    client = TestClient(create_app(backend))
    assert client.get(LOCAL).status_code == 200


def test_the_log_tail_shows_the_last_lines_of_a_local_run(
    client: TestClient, backend: DemoBackend
) -> None:
    # Scenario 5: only the tail, chat-rendered, with the full log one click away.
    detail = backend.issue("acme/telemetry-gateway", 214)
    assert detail is not None and detail.log is not None
    assert len(detail.log.splitlines()) <= LOG_TAIL
    html = client.get(LOCAL).text
    log = panel(html, "Log tail")
    assert "<ol class=log>" in log and "128 passed in 3.40s" in log
    assert 'href="/workers/claude/log"' in log
    remote = panel(client.get(REMOTE).text, "Log tail")
    assert "ci-runner-03" in remote and "holder's manager" in remote


def test_the_log_tail_keeps_only_the_newest_lines() -> None:
    from github_issue_worker.ui.model import tail

    text = "\n".join(str(number) for number in range(1, 501))
    assert tail(text).splitlines() == [str(number) for number in range(301, 501)]
    assert tail("a\nb", 5) == "a\nb"


def test_the_claim_history_panel_lists_versions_newest_first(client: TestClient) -> None:
    # Scenario 6: time, holder, generation, state and step; renewals folded and counted.
    history = panel(client.get(LOCAL).text, "Claim history")
    rows = re.findall(r"<tr><td>(.*?)</tr>", history)
    assert "step" in rows[0] and "implement" in rows[0]
    assert any("renewed" in row and "&times; 12" in row for row in rows)
    assert "claimed" in rows[-1] and "laptop-01ab/claude" in rows[-1]
    assert any("taken over" in row and "office-pc-7f3a/claude" in row for row in rows)
    for row in rows:
        assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d", row)


def test_skipping_labels_the_issue_and_comes_back_with_a_notice(
    client: TestClient, backend: DemoBackend
) -> None:
    # Scenario 7: worker:skip goes on through the backend; the page says what follows.
    response = client.post(f"{LOCAL}/skip", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{LOCAL}?notice=")
    assert "next checkpoint" in unquote(response.headers["location"])
    assert backend.skipped == [("acme/telemetry-gateway", 214)]


def test_a_refused_skip_shows_why(client: TestClient, backend: DemoBackend) -> None:
    backend.refuse_skip = "acme/telemetry-gateway: GitHub refused the label"
    response = client.post(f"{LOCAL}/skip")
    assert response.status_code == 422
    assert "GitHub refused the label" in response.text


def test_releasing_a_local_run_stops_it_like_stop_and_release(
    client: TestClient, backend: DemoBackend
) -> None:
    # Scenario 8, this manager's working claim: the agent is stopped and its worker paused.
    response = client.post(f"{LOCAL}/release", follow_redirects=False)
    assert response.status_code == 303
    assert backend.stopped == ["claude"]
    assert backend.released == []


def test_releasing_another_managers_claim_needs_the_typed_reference(
    client: TestClient, backend: DemoBackend
) -> None:
    # Scenario 8, another manager's claim: refused without the exact reference.
    html = client.get(REMOTE).text
    assert 'name="confirm"' in html and "acme/billing-cli#91" in html
    wrong = client.post(f"{REMOTE}/release", data={"confirm": "acme/billing-cli#9"})
    assert wrong.status_code == 422 and "Type acme/billing-cli#91 exactly" in wrong.text
    missing = client.post(f"{REMOTE}/release")
    assert missing.status_code == 422
    assert backend.released == []
    right = client.post(
        f"{REMOTE}/release", data={"confirm": "acme/billing-cli#91"}, follow_redirects=False
    )
    assert right.status_code == 303
    assert backend.released == [("acme/billing-cli", 91, True)]


def test_releasing_a_held_claim_of_this_manager_without_a_running_agent(
    backend: DemoBackend,
) -> None:
    # Scenario 8: a claim held here whose worker is not running it is released directly.
    held = backend.issue("acme/telemetry-gateway", 214)
    assert held is not None and held.claim is not None
    backend.details[("acme/telemetry-gateway", 214)] = replace(held, log_worker=None)
    client = TestClient(create_app(backend))
    response = client.post(f"{LOCAL}/release", follow_redirects=False)
    assert response.status_code == 303
    assert backend.released == [("acme/telemetry-gateway", 214, False)]
    assert backend.stopped == []


def test_releasing_an_issue_without_a_held_claim_is_refused(
    client: TestClient, backend: DemoBackend
) -> None:
    response = client.post(f"{FREE}/release")
    assert response.status_code == 422
    assert "holds no claim" in response.text
    assert backend.released == [] and backend.stopped == []


def test_the_issue_page_links_its_open_pull_request(client: TestClient) -> None:
    # Scenario 9: a link to the pull request in a new tab, and none without one.
    html = client.get(LOCAL).text
    assert (
        '<a href="https://github.com/acme/telemetry-gateway/pull/230" target="_blank" '
        'rel="noopener noreferrer"'
    ) in html
    assert "Open PR" in html
    assert "Open PR" not in client.get(FREE).text


def test_issues_and_fleet_rows_link_their_issue_page(client: TestClient) -> None:
    # Scenario 10: the page's own link opens in the same tab, besides the GitHub link.
    for path in ("/", "/fleet"):
        html = client.get(path).text
        assert f'<a href="{LOCAL}">' in html, path
        assert f'<a href="{REMOTE}">' in html, path
        assert "https://github.com/acme/telemetry-gateway/issues/214" in html, path


def test_the_live_fragment_carries_the_panels_and_not_the_actions(client: TestClient) -> None:
    fragment = client.get(f"{LOCAL}?fragment=1").text
    assert "<nav>" not in fragment and "data-now=" in fragment
    assert 'aria-label="Claim history"' in fragment
    assert "/skip" not in fragment


def test_the_issue_page_says_which_issue_is_unclaimed(client: TestClient) -> None:
    html = client.get(FREE).text
    assert "Nobody holds it" in panel(html, "Claim")
    assert "No run has finished" in panel(html, "Runs")
    assert "never claimed" in panel(html, "Claim history")


def test_an_issue_detail_is_held_only_while_working_or_paused(backend: DemoBackend) -> None:
    detail = backend.issue("acme/telemetry-gateway", 214)
    assert isinstance(detail, IssueDetail) and detail.held
    assert detail.claim is not None
    assert not replace(detail, claim=replace(detail.claim, state="released")).held
    assert not IssueDetail(detail.issue).held
