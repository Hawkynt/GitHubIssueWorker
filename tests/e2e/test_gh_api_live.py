from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from github_issue_worker.adapters.gh_api import GhApi
from github_issue_worker.application.github_host import GitHubHost
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


def test_sub_issues_are_listed_by_the_live_endpoint() -> None:
    # Read-only: creating sub-issues on every CI run would litter the repository with issues.
    host = GitHubHost(GhApi(), REPOSITORY.removeprefix("repos/"))
    children = host.sub_issues(33)
    assert isinstance(children, list)
    assert all(child.number != 33 for child in children)


def test_linked_branches_are_read_from_the_live_graphql_api() -> None:
    # Issue 71 brought F5.3; its links may change, the query's shape must not.
    host = GitHubHost(GhApi(), REPOSITORY.removeprefix("repos/"))
    names = host.linked_branches(71)
    assert isinstance(names, list) and all(isinstance(name, str) for name in names)


def test_the_live_api_accepts_the_shape_of_create_linked_branch() -> None:
    # A commit that cannot exist: GitHub rejects the link itself, never the mutation's shape,
    # and nothing is created.
    host = GitHubHost(GhApi(), REPOSITORY.removeprefix("repos/"))
    with pytest.raises(GitHubError) as caught:
        host.link_branch(71, "ghiw-e2e-never-created", "0" * 40)
    message = str(caught.value)
    for schema_error in (
        "doesn't exist on type",
        "Variable $",
        "argumentNotAccepted",
        "undefinedField",
    ):
        assert schema_error not in message
