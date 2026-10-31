"""GitHub's REST and GraphQL API through ``gh api -i`` (F0.5, F2.6).

``gh`` authenticates; this adapter only parses. ``-i`` puts the status line and every header on
stdout, also for ``304`` and error responses, for which ``gh`` exits non-zero. The status therefore
comes from the output, never from the exit code. Every response's ``Date`` header feeds the server
clock that claims are judged by.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from github_issue_worker.adapters.home import account_home, tool_environment
from github_issue_worker.adapters.server_clock import ServerClock
from github_issue_worker.ports.github import (
    GhUnavailable,
    GitHubError,
    RateLimited,
    Response,
    next_link,
)

__all__ = ["GhApi", "next_link", "run_gh"]

GhRunner = Callable[[list[str], str | None, dict[str, str]], tuple[int, str, str]]
"""Runs ``gh`` with argv, optional stdin and an environment; returns exit code, stdout, stderr."""

_STATUS = re.compile(r"^HTTP/[\d.]+ (\d{3})")
_DEFAULT_WAIT = timedelta(minutes=1)


def run_gh(argv: list[str], stdin: str | None, env: dict[str, str]) -> tuple[int, str, str]:
    result = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, check=False)
    return result.returncode, result.stdout, result.stderr


class GhApi:
    """A `GitHubApi` over the ``gh`` command line."""

    def __init__(
        self,
        run: GhRunner = run_gh,
        clock: ServerClock | None = None,
        home: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self._run = run
        self.clock = clock or ServerClock()
        self._home = home or account_home()
        self._sleep = sleep
        self._max_retries = max_retries
        self._reset_at: datetime | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        target = f"{path}?{urlencode(params)}" if params else path
        argv = ["gh", "api", "-i", "--method", method, target]
        argv += ["-H", "Accept: application/vnd.github+json"]
        for name, value in (headers or {}).items():
            argv += ["-H", f"{name}: {value}"]
        stdin = None
        if body is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(body)

        env = {
            **tool_environment(self._home),
            "LC_ALL": "C",
            "LANGUAGE": "C",
            "GH_PROMPT_DISABLED": "1",
        }
        attempt = 0
        while True:
            if self._reset_at is not None:
                self._wait_until(self._reset_at)
                self._reset_at = None

            _, stdout, stderr = self._run(argv, stdin, env)
            response = self._parse(stdout)
            if response is None:
                raise GhUnavailable(stderr.strip() or "gh produced no HTTP response")

            if "date" in response.headers:
                self.clock.observe(response.headers["date"])

            if response.status < 400:
                self._reset_at = self._exhausted_reset(response.headers)
                return response

            until = self._rate_limited_until(response)
            if until is not None and attempt < self._max_retries:
                attempt += 1
                self._wait_until(until, minimum=1.0)
                continue
            raise self._error(response, until)

    def _wait_until(self, until: datetime, minimum: float = 0.0) -> None:
        delay = max((until - self.clock.now()).total_seconds(), minimum)
        if delay > 0:
            self._sleep(delay)

    @staticmethod
    def _exhausted_reset(headers: Mapping[str, str]) -> datetime | None:
        if (
            headers.get("x-ratelimit-remaining") == "0"
            and headers.get("x-ratelimit-reset", "").isdigit()
        ):
            return datetime.fromtimestamp(int(headers["x-ratelimit-reset"]), tz=UTC)
        return None

    @staticmethod
    def _parse(stdout: str) -> Response | None:
        text = stdout.replace("\r\n", "\n")
        match = _STATUS.match(text)
        if not match:
            return None

        head, _, payload = text.partition("\n\n")
        headers: dict[str, str] = {}
        for line in head.split("\n")[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()

        payload = payload.strip()
        try:
            body = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            body = payload
        return Response(status=int(match.group(1)), headers=headers, body=body)

    def _error(self, response: Response, until: datetime | None = None) -> GitHubError:
        message = response.body.get("message", "") if isinstance(response.body, dict) else ""
        if until is not None:
            return RateLimited(response.status, message or "rate limited", until)
        return GitHubError(response.status, message or "request failed")

    def _rate_limited_until(self, response: Response) -> datetime | None:
        headers = response.headers
        if response.status not in (403, 429):
            return None
        if "retry-after" in headers and headers["retry-after"].isdigit():
            return self.clock.now() + timedelta(seconds=int(headers["retry-after"]))
        exhausted = self._exhausted_reset(headers)
        if exhausted is not None:
            return exhausted
        if response.status == 429:
            return self.clock.now() + _DEFAULT_WAIT
        return None
