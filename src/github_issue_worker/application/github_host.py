"""`RepositoryHost` over the GitHub REST API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from github_issue_worker.application.poller import parse_comment, parse_issue
from github_issue_worker.domain.issue import Comment, Issue
from github_issue_worker.ports.github import GitHubApi, GitHubError, Response, next_link
from github_issue_worker.ports.repository import (
    CREATED_LABEL_COLOR,
    WORKER_LABELS,
    Check,
    PullRequest,
    RepositoryInfo,
    WaitingRun,
)
from github_issue_worker.product import MARKER_PREFIX

MARKER = f"{MARKER_PREFIX}comment -->"

# The two GraphQL operations of F5.3, converted from GitHub CLI's `gh issue develop`
# (api/queries_branch_issue_reference.go, MIT, see THIRD-PARTY-NOTICE.GitHubCLI.txt).
_LINKED_BRANCHES = """
query linkedBranches($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    id
    issue(number: $number) {
      id
      linkedBranches(first: 100) { nodes { ref { name } } }
    }
  }
}
"""
_CREATE_LINKED_BRANCH = """
mutation createLinkedBranch($input: CreateLinkedBranchInput!) {
  createLinkedBranch(input: $input) { linkedBranch { id ref { name } } }
}
"""


def _pull(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(data["number"]),
        head_sha=data["head"]["sha"],
        url=data.get("html_url", ""),
        mergeable_state=str(data.get("mergeable_state") or "unknown"),
        merged=bool(data.get("merged")),
        title=str(data.get("title") or ""),
        state=str(data.get("state") or "open"),
    )


class GitHubHost:
    def __init__(
        self, api: GitHubApi, repository: str, before_write: Callable[[], None] = lambda: None
    ) -> None:
        self.api = api
        self.repository = repository
        self.before_write = before_write
        self._roles: dict[str, str | None] = {}

    def _graphql(
        self, query: str, variables: dict[str, Any], *, mutation: bool = False
    ) -> dict[str, Any]:
        """The ``data`` of a GraphQL answer. GraphQL reports errors with status 200, in
        ``errors``; those are raised like any failed request."""
        if mutation:
            self.before_write()
        body = self.api.request(
            "POST", "graphql", body={"query": query, "variables": variables}
        ).body
        if not isinstance(body, dict):
            raise GitHubError(200, "GraphQL answered without a JSON object")
        if body.get("errors"):
            messages = "; ".join(str(error.get("message", error)) for error in body["errors"])
            raise GitHubError(200, f"GraphQL: {messages}")
        return dict(body.get("data") or {})

    def _issue_links(self, number: int) -> tuple[str, str, list[str]]:
        """The repository's and the issue's node ids, and the names of the linked branches."""
        owner, name = self.repository.split("/", 1)
        data = self._graphql(_LINKED_BRANCHES, {"owner": owner, "name": name, "number": number})
        repository = data.get("repository") or {}
        issue = repository.get("issue") or {}
        if not repository.get("id") or not issue.get("id"):
            raise GitHubError(404, f"{self.repository}#{number} not found")
        nodes = (issue.get("linkedBranches") or {}).get("nodes") or []
        names = [str(node["ref"]["name"]) for node in nodes if node and node.get("ref")]
        return str(repository["id"]), str(issue["id"]), names

    def linked_branches(self, issue: int) -> list[str]:
        return self._issue_links(issue)[2]

    def link_branch(self, issue: int, branch: str, oid: str) -> None:
        self.before_write()  # a suspended repository is not even asked for the ids
        repository_id, issue_id, _ = self._issue_links(issue)
        data = self._graphql(
            _CREATE_LINKED_BRANCH,
            {
                "input": {
                    "repositoryId": repository_id,
                    "issueId": issue_id,
                    "oid": oid,
                    "name": branch,
                }
            },
            mutation=True,
        )
        if not (data.get("createLinkedBranch") or {}).get("linkedBranch"):
            raise GitHubError(200, f"GraphQL: no linked branch was created for {branch}")

    def _path(self, tail: str) -> str:
        return f"repos/{self.repository}/{tail}" if tail else f"repos/{self.repository}"

    def _write(self, method: str, path: str, *, body: Any = None) -> Response:
        """Check suspension before each mutation, including multi-request operations."""
        self.before_write()
        return self.api.request(method, path, body=body)

    def info(self) -> RepositoryInfo:
        data = self.api.request("GET", self._path("")).body
        methods = tuple(
            method
            for method, key in (
                ("squash", "allow_squash_merge"),
                ("merge", "allow_merge_commit"),
                ("rebase", "allow_rebase_merge"),
            )
            if data.get(key, True)
        )
        return RepositoryInfo(
            default_branch=data["default_branch"],
            can_push=bool((data.get("permissions") or {}).get("push")),
            merge_methods=methods,
        )

    def role(self, login: str) -> str | None:
        if login not in self._roles:
            try:
                data = self.api.request(
                    "GET", self._path(f"collaborators/{quote(login)}/permission")
                ).body
                self._roles[login] = data.get("role_name") or data.get("permission")
            except GitHubError as error:
                if error.status not in (403, 404):
                    raise
                self._roles[login] = None
        return self._roles[login]

    def issue(self, number: int) -> Issue:
        return parse_issue(self.api.request("GET", self._path(f"issues/{number}")).body)

    def open_issues(self) -> list[Issue]:
        """Every open issue, pull requests left out."""
        found: list[Issue] = []
        response = self.api.request(
            "GET", self._path("issues"), params={"state": "open", "per_page": "100"}
        )
        while True:
            found += [
                parse_issue(item) for item in response.body or [] if "pull_request" not in item
            ]
            page = next_link(response.headers)
            if page is None:
                return found
            response = self.api.request("GET", page)

    def comments(self, number: int) -> list[Comment]:
        found: list[Comment] = []
        response = self.api.request(
            "GET", self._path(f"issues/{number}/comments"), params={"per_page": "100"}
        )
        while True:
            found += [parse_comment(item) for item in response.body or []]
            page = next_link(response.headers)
            if page is None:
                return found
            response = self.api.request("GET", page)

    def label_names(self) -> set[str]:
        names: set[str] = set()
        response = self.api.request("GET", self._path("labels"), params={"per_page": "100"})
        while True:
            names |= {label["name"] for label in response.body or []}
            page = next_link(response.headers)
            if page is None:
                return names
            response = self.api.request("GET", page)

    def ensure_worker_labels(self) -> None:
        existing = self.label_names()
        for name, (color, description) in WORKER_LABELS.items():
            if name not in existing:
                body = {"name": name, "color": color, "description": description}
                self._write("POST", self._path("labels"), body=body)

    def set_worker_label(self, issue: int, label: str | None) -> None:
        current = self.api.request("GET", self._path(f"issues/{issue}/labels")).body or []
        for existing in current:
            name = existing["name"]
            if name.startswith("worker:") and name != label and name != "worker:skip":
                self._write("DELETE", self._path(f"issues/{issue}/labels/{quote(name)}"))
        if label and label not in {existing["name"] for existing in current}:
            self._write("POST", self._path(f"issues/{issue}/labels"), body={"labels": [label]})

    def add_labels(self, issue: int, labels: list[str]) -> None:
        if labels:
            self._write("POST", self._path(f"issues/{issue}/labels"), body={"labels": labels})

    def create_label(self, name: str) -> None:
        try:
            self._write(
                "POST",
                self._path("labels"),
                body={"name": name, "color": CREATED_LABEL_COLOR},
            )
        except GitHubError as error:
            # Another manager may have created it since the label list was read (F4.2).
            if error.status == 422 and "already_exists" in str(error):
                return
            raise

    def comment(self, issue: int, body: str) -> None:
        text = body if MARKER_PREFIX in body else f"{body}\n\n{MARKER}"
        self._write("POST", self._path(f"issues/{issue}/comments"), body={"body": text})

    def find_pull_request(self, branch: str) -> PullRequest | None:
        owner = self.repository.split("/", 1)[0]
        pulls = self.api.request(
            "GET", self._path("pulls"), params={"head": f"{owner}:{branch}", "state": "open"}
        ).body
        return self.pull_request(int(pulls[0]["number"])) if pulls else None

    def create_pull_request(self, branch: str, base: str, title: str, body: str) -> PullRequest:
        data = self._write(
            "POST",
            self._path("pulls"),
            body={"title": title, "head": branch, "base": base, "body": body},
        ).body
        return _pull(data)

    def pull_request(self, number: int) -> PullRequest:
        return _pull(self.api.request("GET", self._path(f"pulls/{number}")).body)

    def checks(self, sha: str) -> list[Check]:
        data = self.api.request(
            "GET", self._path(f"commits/{sha}/check-runs"), params={"per_page": "100"}
        ).body
        checks = []
        for run in (data or {}).get("check_runs", []):
            output = run.get("output") or {}
            annotations: tuple[str, ...] = ()
            failed = run.get("conclusion") not in (None, "success", "neutral", "skipped")
            if failed and output.get("annotations_count") and "id" in run:
                notes = self.api.request("GET", self._path(f"check-runs/{run['id']}/annotations"))
                annotations = tuple(
                    str(note.get("message", ""))
                    for note in notes.body or []
                    if note.get("annotation_level") == "failure"
                )
            checks.append(
                Check(
                    name=run["name"],
                    status=run["status"],
                    conclusion=run.get("conclusion"),
                    summary=(output.get("summary") or "")[:2000],
                    annotations=annotations,
                )
            )
        return checks

    def merge(self, number: int, method: str, sha: str, title: str = "", message: str = "") -> str:
        body = {"merge_method": method, "sha": sha}
        if title:
            body["commit_title"] = title
        if message:
            body["commit_message"] = message
        data = self._write("PUT", self._path(f"pulls/{number}/merge"), body=body).body
        return str(data.get("sha", ""))

    def delete_branch(self, branch: str) -> None:
        try:
            self._write("DELETE", self._path(f"git/refs/heads/{branch}"))
        except GitHubError as error:
            if error.status not in (404, 422):  # already gone, e.g. deleted on merge
                raise

    def waiting_for_approval(self, sha: str) -> list[WaitingRun]:
        data = self.api.request(
            "GET",
            self._path("actions/runs"),
            params={"head_sha": sha, "status": "action_required", "per_page": "100"},
        ).body
        # The filter alone is not trusted: a run approved a moment ago may still be listed.
        return [
            WaitingRun(
                int(run["id"]),
                str(run.get("name") or "workflow"),
                run.get("html_url", ""),
                str(run.get("event") or ""),
                (run.get("head_repository") or {}).get("full_name") == self.repository,
            )
            for run in (data or {}).get("workflow_runs", [])
            if run.get("status") == "completed" and run.get("conclusion") == "action_required"
        ]

    def approve_run(self, run: int) -> None:
        self._write("POST", self._path(f"actions/runs/{run}/approve"))

    def branches(self, prefix: str) -> list[str]:
        refs = self.api.request("GET", self._path(f"git/matching-refs/heads/{prefix}")).body
        return [str(ref["ref"]).removeprefix("refs/heads/") for ref in refs or []]

    def pull_requests_for(self, branch: str) -> list[PullRequest]:
        owner = self.repository.split("/", 1)[0]
        pulls = self.api.request(
            "GET",
            self._path("pulls"),
            params={"head": f"{owner}:{branch}", "state": "all", "per_page": "100"},
        ).body
        return [_pull(data) for data in pulls or []]

    def create_sub_issue(self, parent: int, title: str, body: str, labels: list[str]) -> int:
        data = self._write(
            "POST", self._path("issues"), body={"title": title, "body": body, "labels": labels}
        ).body
        self._write(
            "POST", self._path(f"issues/{parent}/sub_issues"), body={"sub_issue_id": data["id"]}
        )
        return int(data["number"])

    def sub_issues(self, parent: int) -> list[Issue]:
        found: list[Issue] = []
        response = self.api.request(
            "GET", self._path(f"issues/{parent}/sub_issues"), params={"per_page": "100"}
        )
        while True:
            found += [parse_issue(item) for item in response.body or []]
            page = next_link(response.headers)
            if page is None:
                return found
            response = self.api.request("GET", page)

    def close_issue(self, issue: int) -> None:
        self._write(
            "PATCH",
            self._path(f"issues/{issue}"),
            body={"state": "closed", "state_reason": "completed"},
        )
