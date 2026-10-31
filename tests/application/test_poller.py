from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from github_issue_worker.application.poller import Poller, PollState
from github_issue_worker.ports.github import Response
from tests.fakes import FakeClock

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
REPO = "Hawkynt/ShiftDiff"


def issue_json(number: int, updated: str = "2026-10-30T12:05:00Z", **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": None,
        "state": "open",
        "user": {"login": "Hawkynt", "type": "User"},
        "labels": [{"name": "bug"}],
        "created_at": "2026-10-30T12:01:00Z",
        "updated_at": updated,
        **extra,
    }


def comment_json(id: int, issue: int = 1, updated: str = "2026-10-30T12:06:00Z") -> dict[str, Any]:
    return {
        "id": id,
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{issue}",
        "user": {"login": "dependabot[bot]", "type": "Bot"},
        "body": "bump",
        "created_at": updated,
        "updated_at": updated,
    }


class ScriptedGitHub:
    def __init__(self, replies: dict[str, list[Response]]) -> None:
        self.replies = replies
        self.requests: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        self.requests.append((path, dict(params or {}), dict(headers or {})))
        return self.replies[path].pop(0)


def poller(github: ScriptedGitHub, clock: FakeClock | None = None) -> Poller:
    return Poller(github=github, clock=clock or FakeClock(NOW))


def test_the_first_watch_records_the_position_and_reports_nothing() -> None:
    github = ScriptedGitHub({})
    result, state = poller(github).poll(REPO, PollState(), process_backlog=False)
    assert result.issues == () and result.comments == ()
    assert state.watched_since == NOW
    assert state.issues_since == NOW and state.comments_since == NOW
    assert github.requests == []


def test_the_first_watch_with_backlog_reads_everything() -> None:
    github = ScriptedGitHub(
        {
            f"repos/{REPO}/issues": [Response(200, {"etag": '"i1"'}, [issue_json(1)])],
            f"repos/{REPO}/issues/comments": [Response(200, {"etag": '"c1"'}, [])],
        }
    )
    result, state = poller(github).poll(REPO, PollState(), process_backlog=True)
    assert [issue.number for issue in result.issues] == [1]
    assert state.watched_since == datetime.min.replace(tzinfo=UTC)
    assert "since" not in github.requests[0][1]


def test_a_poll_sends_since_and_etags_and_parses_issues_and_comments() -> None:
    since = NOW - timedelta(minutes=1)
    github = ScriptedGitHub(
        {
            f"repos/{REPO}/issues": [
                Response(
                    200, {"etag": '"i2"'}, [issue_json(7, pull_request={"url": "x"}), issue_json(8)]
                )
            ],
            f"repos/{REPO}/issues/comments": [
                Response(200, {"etag": '"c2"'}, [comment_json(99, issue=8)])
            ],
        }
    )
    state = PollState(
        watched_since=since,
        issues_since=since,
        comments_since=since,
        issues_etag='"i1"',
        comments_etag='"c1"',
    )
    result, new_state = poller(github).poll(REPO, state, process_backlog=False)

    _path, params, headers = github.requests[0]
    assert params["since"] == "2026-10-30T11:59:00Z"
    assert params["state"] == "all" and params["sort"] == "updated" and params["direction"] == "asc"
    assert headers == {"If-None-Match": '"i1"'}
    assert github.requests[1][2] == {"If-None-Match": '"c1"'}

    first, second = result.issues
    assert first.is_pull_request and not second.is_pull_request
    assert second.body == "" and second.labels == frozenset({"bug"})
    (comment,) = result.comments
    assert comment.issue == 8 and comment.author.is_bot
    assert new_state.issues_etag == '"i2"' and new_state.comments_etag == '"c2"'
    # Each stream keeps its own cursor: a shared one could skip an issue updated between the
    # issues request and the comments request.
    assert new_state.issues_since == datetime(2026, 10, 30, 12, 5, 0, tzinfo=UTC)
    assert new_state.comments_since == datetime(2026, 10, 30, 12, 6, 0, tzinfo=UTC)


def test_not_modified_reports_nothing_and_keeps_the_state() -> None:
    github = ScriptedGitHub(
        {
            f"repos/{REPO}/issues": [Response(304, {"etag": '"i1"'})],
            f"repos/{REPO}/issues/comments": [Response(304, {"etag": '"c1"'})],
        }
    )
    state = PollState(
        watched_since=NOW,
        issues_since=NOW,
        comments_since=NOW,
        issues_etag='"i1"',
        comments_etag='"c1"',
    )
    result, new_state = poller(github).poll(REPO, state, process_backlog=False)
    assert result.issues == () and result.comments == ()
    from dataclasses import replace

    assert new_state == replace(state, last_polled_at=NOW)


def test_all_pages_are_read_and_only_the_first_is_conditional() -> None:
    link = '<https://api.github.com/repositories/1/issues?page=2&since=x>; rel="next"'
    github = ScriptedGitHub(
        {
            f"repos/{REPO}/issues": [
                Response(200, {"etag": '"p1"', "link": link}, [issue_json(1)])
            ],
            "repositories/1/issues?page=2&since=x": [Response(200, {}, [issue_json(2)])],
            f"repos/{REPO}/issues/comments": [Response(304, {})],
        }
    )
    state = PollState(watched_since=NOW, issues_since=NOW, comments_since=NOW, issues_etag='"p0"')
    result, new_state = poller(github).poll(REPO, state, process_backlog=False)
    assert [issue.number for issue in result.issues] == [1, 2]
    assert github.requests[1][2] == {}
    assert new_state.issues_etag == '"p1"'


def test_last_successful_poll_advances_on_unchanged_responses_and_survives_restart(
    tmp_path: Path,
) -> None:
    from github_issue_worker.adapters.state_store import JsonStateStore

    clock = FakeClock(NOW)
    api = ScriptedGitHub(
        {
            f"repos/{REPO}/issues": [Response(304, {}), Response(304, {})],
            f"repos/{REPO}/issues/comments": [Response(304, {}), Response(304, {})],
        }
    )
    state = PollState(watched_since=NOW, issues_since=NOW)
    path = tmp_path / "state.json"
    store = JsonStateStore(path)
    for _ in range(2):
        clock.advance(timedelta(minutes=5))
        _, state = poller(api, clock).poll(REPO, state, False)
        store.set_poll_state(REPO, state)
        assert JsonStateStore(path).poll_state(REPO).last_polled_at == clock.now()
        assert state.watched_since == NOW and state.issues_since == NOW


def test_failed_poll_does_not_advance_last_successful_poll(tmp_path: Path) -> None:
    import pytest

    from github_issue_worker.adapters.state_store import JsonStateStore

    store = JsonStateStore(tmp_path / "state.json")
    state = PollState(watched_since=NOW, last_polled_at=NOW)
    store.set_poll_state(REPO, state)
    api = ScriptedGitHub({f"repos/{REPO}/issues": [Response(304, {})]})
    clock = FakeClock(NOW + timedelta(minutes=5))
    with pytest.raises(KeyError):
        _, updated = poller(api, clock).poll(REPO, state, False)
        store.set_poll_state(REPO, updated)
    assert store.poll_state(REPO).last_polled_at == NOW


def test_watch_initialization_is_not_reported_as_a_successful_poll() -> None:
    _, state = poller(ScriptedGitHub({})).poll(REPO, PollState(), False)
    assert state.last_polled_at is None
