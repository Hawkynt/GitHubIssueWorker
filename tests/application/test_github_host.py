from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from github_issue_worker.application.github_host import GitHubHost
from github_issue_worker.ports.github import GitHubError, Response
from github_issue_worker.ports.repository import WORKER_LABELS

REPO = "Hawkynt/ShiftDiff"


class RecordingApi:
    def __init__(self, routes: dict[tuple[str, str], Any] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[tuple[str, str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        self.calls.append((method, path, body))
        reply = self.routes.get((method, path))
        if isinstance(reply, Exception):
            raise reply
        return Response(200, {}, reply)


def host(routes: dict[tuple[str, str], Any]) -> tuple[GitHubHost, RecordingApi]:
    api = RecordingApi(routes)
    return GitHubHost(api, REPO), api


def test_info_reads_branch_push_and_merge_methods() -> None:
    gh, _ = host(
        {
            ("GET", f"repos/{REPO}"): {
                "default_branch": "main",
                "permissions": {"push": True},
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            }
        }
    )
    info = gh.info()
    assert (info.default_branch, info.can_push, info.merge_methods) == ("main", True, ("squash",))


def test_role_prefers_role_name_and_is_cached() -> None:
    path = f"repos/{REPO}/collaborators/alice/permission"
    gh, api = host({("GET", path): {"permission": "write", "role_name": "maintain"}})
    assert gh.role("alice") == "maintain"
    assert gh.role("alice") == "maintain"
    assert len(api.calls) == 1


def test_a_non_collaborator_has_no_role() -> None:
    path = f"repos/{REPO}/collaborators/mallory/permission"
    gh, _ = host({("GET", path): GitHubError(404, "Not Found")})
    assert gh.role("mallory") is None


def test_missing_worker_labels_are_created() -> None:
    gh, api = host({("GET", f"repos/{REPO}/labels"): [{"name": "worker:working"}, {"name": "bug"}]})
    gh.ensure_worker_labels()
    created = {body["name"] for method, path, body in api.calls if method == "POST"}
    assert created == set(WORKER_LABELS) - {"worker:working"}


def test_a_triage_label_is_created_on_the_repository() -> None:
    gh, api = host({})
    gh.create_label("invented")
    assert ("POST", f"repos/{REPO}/labels", {"name": "invented", "color": "ededed"}) in api.calls


def test_creating_a_label_another_manager_created_first_is_ignored() -> None:
    gh, api = host({("POST", f"repos/{REPO}/labels"): GitHubError(422, "already_exists")})
    gh.create_label("invented")
    assert ("POST", f"repos/{REPO}/labels", {"name": "invented", "color": "ededed"}) in api.calls


def test_creating_a_label_with_an_invalid_name_still_raises() -> None:
    gh, _ = host({("POST", f"repos/{REPO}/labels"): GitHubError(422, "invalid name")})
    with pytest.raises(GitHubError):
        gh.create_label("invented")


def test_setting_a_worker_label_removes_the_other_worker_labels_but_not_skip() -> None:
    labels = [{"name": "bug"}, {"name": "worker:queued"}, {"name": "worker:skip"}]
    gh, api = host({("GET", f"repos/{REPO}/issues/7/labels"): labels})
    gh.set_worker_label(7, "worker:working")
    assert ("DELETE", f"repos/{REPO}/issues/7/labels/worker%3Aqueued", None) in api.calls
    assert not any("worker%3Askip" in path for _, path, _ in api.calls)
    assert ("POST", f"repos/{REPO}/issues/7/labels", {"labels": ["worker:working"]}) in api.calls


def test_comments_carry_the_marker() -> None:
    gh, api = host({})
    gh.comment(7, "Which way?")
    (_, _, body) = api.calls[0]
    assert body["body"].startswith("Which way?") and "<!-- ghiw:" in body["body"]


def test_merge_passes_the_expected_head() -> None:
    gh, api = host({("PUT", f"repos/{REPO}/pulls/3/merge"): {"sha": "m1", "merged": True}})
    assert gh.merge(3, "squash", "abc") == "m1"
    assert api.calls[0][2] == {"merge_method": "squash", "sha": "abc"}


@pytest.mark.parametrize("status", [404, 422])
def test_a_branch_that_is_already_gone_is_fine(status: int) -> None:
    # with delete_branch_on_merge GitHub removes it first and answers 404 "Reference does not exist"
    path = f"repos/{REPO}/git/refs/heads/issue/7-x"
    gh, _ = host({("DELETE", path): GitHubError(status, "Reference does not exist")})
    gh.delete_branch("issue/7-x")


def test_checks_are_read_from_check_runs_with_failure_annotations() -> None:
    runs = {
        "check_runs": [
            {
                "id": 9,
                "name": "CI",
                "status": "completed",
                "conclusion": "failure",
                "output": {"summary": "2 tests failed", "annotations_count": 2},
            },
            {
                "id": 8,
                "name": "Lint",
                "status": "completed",
                "conclusion": "success",
                "output": {"summary": None, "annotations_count": 1},
            },
        ]
    }
    notes = [
        {"annotation_level": "failure", "message": "The job was not started because ..."},
        {"annotation_level": "notice", "message": "ubuntu-latest will migrate"},
    ]
    gh, api = host(
        {
            ("GET", f"repos/{REPO}/commits/abc/check-runs"): runs,
            ("GET", f"repos/{REPO}/check-runs/9/annotations"): notes,
        }
    )
    ci, lint = gh.checks("abc")
    assert (ci.name, ci.conclusion, ci.summary) == ("CI", "failure", "2 tests failed")
    assert ci.annotations == ("The job was not started because ...",)
    assert lint.annotations == ()  # annotations are only read for failed runs
    assert not any("check-runs/8" in path for _, path, _ in api.calls)


def test_unexpected_errors_propagate() -> None:
    path = f"repos/{REPO}/collaborators/x/permission"
    gh, _ = host({("GET", path): GitHubError(500, "boom")})
    with pytest.raises(GitHubError):
        gh.role("x")


def test_open_issues_leave_pull_requests_out() -> None:
    issue = {
        "number": 3,
        "title": "t",
        "body": None,
        "state": "open",
        "user": {"login": "Hawkynt", "type": "User"},
        "labels": [],
        "created_at": "2026-10-30T12:00:00Z",
        "updated_at": "2026-10-30T12:00:00Z",
    }
    gh, _ = host(
        {("GET", f"repos/{REPO}/issues"): [issue, {**issue, "number": 4, "pull_request": {}}]}
    )
    assert [found.number for found in gh.open_issues()] == [3]


ISSUE_JSON = {
    "number": 40,
    "title": "Export CSV",
    "body": "b",
    "state": "closed",
    "state_reason": "completed",
    "user": {"login": "Hawkynt", "type": "User"},
    "labels": [],
    "created_at": "2026-10-30T12:00:00Z",
    "updated_at": "2026-10-30T12:00:00Z",
}


def test_a_sub_issue_is_created_with_its_labels_and_attached_to_its_parent() -> None:
    created = {**ISSUE_JSON, "id": 9001, "state": "open", "state_reason": None}
    gh, api = host({("POST", f"repos/{REPO}/issues"): created})
    assert gh.create_sub_issue(33, "Export CSV", "b", ["agent:codex", "worker:queued"]) == 40
    assert api.calls[0] == (
        "POST",
        f"repos/{REPO}/issues",
        {"title": "Export CSV", "body": "b", "labels": ["agent:codex", "worker:queued"]},
    )
    assert api.calls[1] == ("POST", f"repos/{REPO}/issues/33/sub_issues", {"sub_issue_id": 9001})


def test_sub_issues_are_listed_with_why_they_were_closed() -> None:
    gh, _ = host({("GET", f"repos/{REPO}/issues/33/sub_issues"): [ISSUE_JSON]})
    (child,) = gh.sub_issues(33)
    assert (child.number, child.state, child.state_reason) == (40, "closed", "completed")


def test_the_split_label_is_one_of_the_worker_labels() -> None:
    assert "worker:split" in WORKER_LABELS


def test_runs_waiting_for_approval_are_found_and_approved() -> None:
    gh, api = host(
        {
            ("GET", f"repos/{REPO}/actions/runs"): {
                "workflow_runs": [
                    {
                        "id": 7,
                        "name": "CI",
                        "event": "pull_request",
                        "status": "completed",
                        "conclusion": "action_required",
                        "html_url": "https://x/runs/7",
                        "head_repository": {"full_name": "someone/fork"},
                    },
                    {
                        "id": 9,
                        "name": "CI",
                        "event": "pull_request",
                        "status": "completed",
                        "conclusion": "action_required",
                        "html_url": "https://x/runs/9",
                        "head_repository": {"full_name": REPO},
                    },
                    # approved a moment ago and running: no longer waiting, whatever the filter
                    {
                        "id": 8,
                        "name": "CI",
                        "event": "pull_request",
                        "status": "in_progress",
                        "conclusion": None,
                        "html_url": "https://x/runs/8",
                    },
                ]
            }
        }
    )
    fork, own = gh.waiting_for_approval("abc")
    assert (fork.id, fork.name, fork.url, fork.own) == (7, "CI", "https://x/runs/7", False)
    assert fork.event == "pull_request"
    assert (own.id, own.own) == (9, True)
    gh.approve_run(7)
    assert ("POST", f"repos/{REPO}/actions/runs/7/approve", None) in api.calls


@pytest.mark.parametrize(
    "operation",
    [
        lambda h: h.ensure_worker_labels(),
        lambda h: h.set_worker_label(1, "worker:queued"),
        lambda h: h.set_worker_label(1, None),
        lambda h: h.add_labels(1, ["bug"]),
        lambda h: h.create_label("bug"),
        lambda h: h.comment(1, "text"),
        lambda h: h.create_pull_request("issue/1", "main", "+ title", "body"),
        lambda h: h.merge(1, "squash", "head"),
        lambda h: h.delete_branch("issue/1"),
        lambda h: h.approve_run(1),
        lambda h: h.create_sub_issue(1, "title", "body", []),
        lambda h: h.close_issue(1),
        lambda h: h.link_branch(1, "issue/1", "a" * 40),
    ],
    ids=[
        "labels",
        "set-label",
        "remove-label",
        "add-label",
        "create-label",
        "comment",
        "pull",
        "merge",
        "branch",
        "approve",
        "sub-issue",
        "close",
        "link",
    ],
)
def test_given_suspension_when_a_host_mutation_is_attempted_then_no_write_reaches_the_api(
    operation: Callable[[GitHubHost], object],
) -> None:
    from github_issue_worker.ports.repository import RepositorySuspended

    api = RecordingApi({("GET", f"repos/{REPO}/issues/1/labels"): [{"name": "worker:working"}]})

    def suspended() -> None:
        raise RepositorySuspended("lost push access")

    gh = GitHubHost(api, REPO, before_write=suspended)
    with pytest.raises(RepositorySuspended):
        operation(gh)
    assert all(method == "GET" for method, _, _ in api.calls)


def test_given_suspension_between_sub_issue_writes_when_attaching_then_no_second_write() -> None:
    from github_issue_worker.ports.repository import RepositorySuspended

    api = RecordingApi({("POST", f"repos/{REPO}/issues"): {"id": 123, "number": 2}})

    def require_access() -> None:
        if api.calls:
            raise RepositorySuspended("lost push access")

    gh = GitHubHost(api, REPO, before_write=require_access)
    with pytest.raises(RepositorySuspended):
        gh.create_sub_issue(1, "title", "body", [])
    assert len(api.calls) == 1


class GraphQLApi:
    """Answers ``POST graphql`` by the operation the query names; records every request."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Response:
        assert (method, path) == ("POST", "graphql")
        self.calls.append(body)
        for operation, answer in self.answers.items():
            if operation in body["query"]:
                if isinstance(answer, Exception):
                    raise answer
                return Response(200, {}, answer)
        raise AssertionError(f"unexpected query: {body['query']}")


LINKED = {
    "data": {
        "repository": {
            "id": "R_repo",
            "issue": {
                "id": "I_issue",
                "linkedBranches": {
                    "nodes": [
                        {"ref": {"name": "issue/42-retry-on-timeout"}},
                        {"ref": None},
                    ]
                },
            },
        }
    }
}


def test_linked_branches_reads_the_issues_development_panel() -> None:
    api = GraphQLApi({"linkedBranches": LINKED})
    gh = GitHubHost(api, REPO)

    assert gh.linked_branches(42) == ["issue/42-retry-on-timeout"]
    (call,) = api.calls
    assert call["variables"] == {"owner": "Hawkynt", "name": "ShiftDiff", "number": 42}


def test_linking_sends_create_linked_branch_for_the_repository_issue_and_branch() -> None:
    created = {
        "data": {
            "createLinkedBranch": {
                "linkedBranch": {"id": "LB_1", "ref": {"name": "issue/42-retry-on-timeout"}}
            }
        }
    }
    api = GraphQLApi({"createLinkedBranch": created, "linkedBranches": LINKED})
    gh = GitHubHost(api, REPO)

    gh.link_branch(42, "issue/42-retry-on-timeout", "a" * 40)

    mutation = api.calls[-1]
    assert "createLinkedBranch(input:" in mutation["query"]
    assert mutation["variables"] == {
        "input": {
            "repositoryId": "R_repo",
            "issueId": "I_issue",
            "oid": "a" * 40,
            "name": "issue/42-retry-on-timeout",
        }
    }


def test_a_graphql_error_with_status_200_is_a_failed_link() -> None:
    rejected = {
        "data": {"createLinkedBranch": None},
        "errors": [{"type": "UNPROCESSABLE", "message": "Name already exists on this repository"}],
    }
    api = GraphQLApi({"createLinkedBranch": rejected, "linkedBranches": LINKED})
    gh = GitHubHost(api, REPO)

    with pytest.raises(GitHubError, match="Name already exists"):
        gh.link_branch(42, "issue/42-retry-on-timeout", "a" * 40)


def test_a_link_answer_without_a_linked_branch_is_a_failed_link() -> None:
    empty = {"data": {"createLinkedBranch": {"linkedBranch": None}}}
    api = GraphQLApi({"createLinkedBranch": empty, "linkedBranches": LINKED})
    gh = GitHubHost(api, REPO)

    with pytest.raises(GitHubError, match="no linked branch"):
        gh.link_branch(42, "issue/42-retry-on-timeout", "a" * 40)


def test_a_graphql_error_reading_the_links_is_raised_not_read_as_unlinked() -> None:
    api = GraphQLApi({"linkedBranches": {"data": None, "errors": [{"message": "Bad credentials"}]}})
    gh = GitHubHost(api, REPO)

    with pytest.raises(GitHubError, match="Bad credentials"):
        gh.linked_branches(42)
