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


def api(
    gh: FakeGh,
    local: FakeClock | None = None,
    sleeps: list[float] | None = None,
    max_retries: int = 3,
) -> tuple[GhApi, ServerClock]:
    local_clock = local or FakeClock(LOCAL)
    clock = ServerClock(local=local_clock)

    def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        local_clock.advance(timedelta(seconds=seconds))

    return (
        GhApi(
            run=gh,
            clock=clock,
            home=Path("/home/me"),
            sleep=fake_sleep,
            max_retries=max_retries,
        ),
        clock,
    )


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


@pytest.mark.parametrize("status", ["403 Forbidden", "429 Too Many Requests"])
def test_retry_after_backs_off_and_retries_until_the_request_succeeds(status: str) -> None:
    headers = {"Date": "Fri, 30 Oct 2026 13:45:10 GMT", "Retry-After": "60"}
    gh = FakeGh(
        (1, http(status, headers, '{"message": "secondary rate limit"}'), ""),
        (0, http("200 OK", {}, '[{"number": 1}]'), ""),
    )
    sleeps: list[float] = []
    response = api(gh, sleeps=sleeps)[0].request("GET", "repos/o/r/issues")
    assert response.status == 200
    assert response.body == [{"number": 1}]
    assert sleeps == [60.0]
    assert len(gh.calls) == 2


@pytest.mark.parametrize("status", ["403 Forbidden", "429 Too Many Requests"])
def test_an_exhausted_primary_limit_backs_off_until_reset_and_retries(status: str) -> None:
    reset = int((LOCAL + timedelta(minutes=30)).timestamp())
    headers = {
        "Date": "Fri, 30 Oct 2026 13:45:10 GMT",
        "X-Ratelimit-Remaining": "0",
        "X-Ratelimit-Reset": str(reset),
    }
    gh = FakeGh(
        (1, http(status, headers, '{"message": "API rate limit exceeded"}'), ""),
        (0, http("200 OK", {"X-Ratelimit-Remaining": "4999"}, '{"ok": true}'), ""),
    )
    local = FakeClock(LOCAL + timedelta(hours=2))
    sleeps: list[float] = []
    response = api(gh, local=local, sleeps=sleeps)[0].request("GET", "repos/o/r/issues")
    assert response.status == 200
    assert response.body == {"ok": True}
    assert sleeps == [1800.0]
    assert len(gh.calls) == 2


@pytest.mark.parametrize(
    ("status", "body"),
    [("200 OK", "[]"), ("304 Not Modified", "")],
)
def test_exhausted_remaining_on_a_response_waits_until_reset_before_the_next_request(
    status: str, body: str
) -> None:
    reset = int((LOCAL + timedelta(seconds=45)).timestamp())
    headers = {
        "Date": "Fri, 30 Oct 2026 13:45:10 GMT",
        "X-Ratelimit-Remaining": "0",
        "X-Ratelimit-Reset": str(reset),
    }
    gh = FakeGh(
        (0, http(status, headers, body), ""),
        (0, http("200 OK", {"X-Ratelimit-Remaining": "4999"}, '[{"id": 1}]'), ""),
        (0, http("200 OK", {"X-Ratelimit-Remaining": "4998"}, '[{"id": 2}]'), ""),
    )
    sleeps: list[float] = []
    client, _ = api(gh, sleeps=sleeps)
    first = client.request("GET", "repos/o/r/issues")
    assert first.status == int(status.split()[0])
    assert sleeps == []

    second = client.request("GET", "repos/o/r/issues/comments")
    assert second.status == 200
    assert second.body == [{"id": 1}]
    assert sleeps == [45.0]

    third = client.request("GET", "repos/o/r/issues/comments")
    assert third.status == 200
    assert sleeps == [45.0]


def test_exhausted_remaining_does_not_sleep_when_reset_has_already_passed() -> None:
    reset = int((LOCAL + timedelta(seconds=10)).timestamp())
    headers = {
        "Date": "Fri, 30 Oct 2026 13:45:10 GMT",
        "X-Ratelimit-Remaining": "0",
        "X-Ratelimit-Reset": str(reset),
    }
    gh = FakeGh(
        (0, http("200 OK", headers, "[]"), ""),
        (0, http("200 OK", {"X-Ratelimit-Remaining": "4999"}, "[]"), ""),
    )
    local = FakeClock(LOCAL)
    sleeps: list[float] = []
    client, _ = api(gh, local=local, sleeps=sleeps)
    client.request("GET", "repos/o/r/issues")
    local.advance(timedelta(seconds=15))
    client.request("GET", "repos/o/r/issues/comments")
    assert sleeps == []


def test_a_429_without_hints_waits_a_minute_and_retries() -> None:
    gh = FakeGh(
        (1, http("429 Too Many Requests", {}, "{}"), ""),
        (0, http("200 OK", {}, '{"ok": true}'), ""),
    )
    sleeps: list[float] = []
    response = api(gh, sleeps=sleeps)[0].request("GET", "x")
    assert response.status == 200
    assert response.body == {"ok": True}
    assert sleeps == [60.0]
    assert len(gh.calls) == 2


def test_a_plain_403_is_an_error_not_a_rate_limit() -> None:
    gh = FakeGh(
        (1, http("403 Forbidden", {"X-Ratelimit-Remaining": "4000"}, '{"message": "no"}'), "")
    )
    sleeps: list[float] = []
    with pytest.raises(GitHubError) as caught:
        api(gh, sleeps=sleeps)[0].request("GET", "x")
    assert not isinstance(caught.value, RateLimited)
    assert sleeps == []
    assert len(gh.calls) == 1


def test_repeated_rate_limits_raise_rate_limited_after_retries_are_exhausted() -> None:
    reset = int((LOCAL + timedelta(minutes=30)).timestamp())
    headers = {"X-Ratelimit-Remaining": "0", "X-Ratelimit-Reset": str(reset)}
    reply = (1, http("403 Forbidden", headers, '{"message": "API rate limit exceeded"}'), "")
    gh = FakeGh(reply, reply, reply)
    sleeps: list[float] = []
    with pytest.raises(RateLimited) as caught:
        api(gh, sleeps=sleeps, max_retries=2)[0].request("GET", "repos/o/r/issues")
    assert caught.value.until == LOCAL + timedelta(minutes=30)
    assert len(sleeps) == 2
    assert len(gh.calls) == 3


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
    assert gh.environments[0]["HOME"] == str(Path("/home/me"))
    assert gh.environments[0]["LC_ALL"] == "C"
