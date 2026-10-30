from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_issue_worker.adapters.gh_api import GhApi, next_link
from github_issue_worker.adapters.server_clock import ServerClock
from github_issue_worker.ports.github import GhUnavailable, GitHubError, RateLimited
from tests.fakes import FakeClock

LOCAL = datetime(2026, 10, 30, 13, 45, 10, tzinfo=UTC)


def http(status: str, headers: dict[str, str], body: str = "") -> str:
    lines = [f"HTTP/2.0 {status}", *(f"{k}: {v}" for k, v in headers.items()), "", body]
    return "\r\n".join(lines)


class FakeGh:
    def __init__(self, *replies: tuple[int, str, str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[str], str | None]] = []
        self.environments: list[dict[str, str]] = []

    def __call__(
        self, argv: list[str], stdin: str | None, env: dict[str, str]
    ) -> tuple[int, str, str]:
        self.calls.append((argv, stdin))
        self.environments.append(env)
        return self.replies.pop(0)


def api(gh: FakeGh, local: FakeClock | None = None) -> tuple[GhApi, ServerClock]:
    clock = ServerClock(local=local or FakeClock(LOCAL))
    return GhApi(run=gh, clock=clock, home=Path("/home/me")), clock


def test_a_200_returns_status_lowercased_headers_and_json() -> None:
    body = json.dumps([{"number": 1}])
    gh = FakeGh(
        (0, http("200 OK", {"Etag": 'W/"abc"', "Date": "Fri, 30 Oct 2026 13:45:10 GMT"}, body), "")
    )
    response = api(gh)[0].request("GET", "repos/o/r/issues", params={"state": "all"})
    assert response.status == 200
    assert response.headers["etag"] == 'W/"abc"'
    assert response.body == [{"number": 1}]
    argv = gh.calls[0][0]
    assert argv[:4] == ["gh", "api", "-i", "--method"]
    assert "repos/o/r/issues?state=all" in argv


def test_a_304_is_a_response_even_though_gh_exits_non_zero() -> None:
    gh = FakeGh((1, http("304 Not Modified", {"Etag": '"abc"'}), "gh: HTTP 304\n"))
    response = api(gh)[0].request("GET", "repos/o/r/issues", headers={"If-None-Match": '"abc"'})
    assert response.status == 304
    assert response.body is None
    assert 'If-None-Match: "abc"' in gh.calls[0][0]


def test_every_response_feeds_the_server_clock() -> None:
    local = FakeClock(LOCAL + timedelta(days=1))  # local clock a day fast
    gh = FakeGh((0, http("200 OK", {"Date": "Fri, 30 Oct 2026 13:45:10 GMT"}, "{}"), ""))
    client, clock = api(gh, local)
    client.request("GET", "rate_limit")
    assert clock.now() == LOCAL


def test_an_error_status_raises_with_the_message() -> None:
    body = json.dumps({"message": "Not Found"})
    gh = FakeGh((1, http("404 Not Found", {}, body), "gh: Not Found (HTTP 404)\n"))
    with pytest.raises(GitHubError) as caught:
        api(gh)[0].request("GET", "repos/o/r/issues/9")
    assert caught.value.status == 404
    assert "Not Found" in str(caught.value)


def test_retry_after_is_a_rate_limit_until_server_time_plus_delay() -> None:
    headers = {"Date": "Fri, 30 Oct 2026 13:45:10 GMT", "Retry-After": "60"}
    gh = FakeGh((1, http("403 Forbidden", headers, '{"message": "secondary rate limit"}'), ""))
    with pytest.raises(RateLimited) as caught:
        api(gh)[0].request("GET", "repos/o/r/issues")
    assert caught.value.until == LOCAL + timedelta(seconds=60)


def test_an_exhausted_primary_limit_is_a_rate_limit_until_reset() -> None:
    reset = int((LOCAL + timedelta(minutes=30)).timestamp())
    headers = {"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": str(reset)}
    gh = FakeGh((1, http("403 Forbidden", headers, '{"message": "API rate limit exceeded"}'), ""))
    with pytest.raises(RateLimited) as caught:
        api(gh)[0].request("GET", "repos/o/r/issues")
    assert caught.value.until == LOCAL + timedelta(minutes=30)


def test_a_429_without_hints_waits_a_minute() -> None:
    gh = FakeGh((1, http("429 Too Many Requests", {}, "{}"), ""))
    with pytest.raises(RateLimited) as caught:
        api(gh)[0].request("GET", "x")
    assert caught.value.until == LOCAL + timedelta(minutes=1)


def test_a_plain_403_is_an_error_not_a_rate_limit() -> None:
    gh = FakeGh(
        (1, http("403 Forbidden", {"X-Ratelimit-Remaining": "4000"}, '{"message": "no"}'), "")
    )
    with pytest.raises(GitHubError) as caught:
        api(gh)[0].request("GET", "x")
    assert not isinstance(caught.value, RateLimited)


def test_no_http_response_at_all_means_gh_is_unusable() -> None:
    gh = FakeGh((1, "", "To get started with GitHub CLI, please run:  gh auth login\n"))
    with pytest.raises(GhUnavailable, match="gh auth login"):
        api(gh)[0].request("GET", "user")


def test_a_json_body_is_sent_on_stdin() -> None:
    gh = FakeGh((0, http("201 Created", {}, '{"id": 7}'), ""))
    response = api(gh)[0].request("POST", "repos/o/r/issues/1/comments", body={"body": "hi"})
    argv, stdin = gh.calls[0]
    assert argv[argv.index("--input") : argv.index("--input") + 2] == ["--input", "-"]
    assert json.loads(stdin or "") == {"body": "hi"}
    assert response.body == {"id": 7}


def test_next_link_follows_pagination() -> None:
    link = (
        '<https://api.github.com/repositories/1/issues?page=2>; rel="next", '
        '<https://api.github.com/repositories/1/issues?page=5>; rel="last"'
    )
    assert next_link({"link": link}) == "repositories/1/issues?page=2"
    assert next_link({}) is None


def test_gh_runs_with_the_account_home_not_a_relocated_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/me/ClaudeCodePortable/profiles/default")
    gh = FakeGh((0, http("200 OK", {}, "{}"), ""))
    api(gh)[0].request("GET", "user")
    assert gh.environments[0]["HOME"] == "/home/me"
    assert gh.environments[0]["LC_ALL"] == "C"
