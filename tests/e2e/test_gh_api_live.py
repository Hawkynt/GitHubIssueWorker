from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.adapters.gh_api import GhApi
from github_issue_worker.ports.github import GitHubError

pytestmark = pytest.mark.e2e

REPOSITORY = "repos/Hawkynt/GitHubIssueWorker"


def test_conditional_polling_against_github() -> None:
    api = GhApi()
    first = api.request("GET", f"{REPOSITORY}/issues", params={"state": "all", "per_page": "5"})
    assert first.status == 200
    assert isinstance(first.body, list)
    assert abs(api.clock.now() - datetime.now(UTC)) < timedelta(minutes=5)

    again = api.request(
        "GET",
        f"{REPOSITORY}/issues",
        params={"state": "all", "per_page": "5"},
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert again.status == 304


def test_a_missing_issue_is_a_404() -> None:
    with pytest.raises(GitHubError) as caught:
        GhApi().request("GET", f"{REPOSITORY}/issues/999999")
    assert caught.value.status == 404
