"""Branches and worktrees of finished issues go, remotely and locally (F5.5).

The manager deletes an issue's branch and worktree itself when it merges the pull request. This
sweep catches the rest: a pull request merged or closed by a person, an issue closed by hand, a
run that ended before its cleanup, or an issue another manager took over. Nothing anybody holds a
claim on is touched, and a branch whose pull request is still open stays.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from github_issue_worker.ports.repository import RepositoryHost
from github_issue_worker.ports.workspace import WorkspacePort

log = logging.getLogger(__name__)

BRANCH_PREFIX = "issue/"
_NUMBER = re.compile(r"^issue/(\d+)(?:-|$)")


def issue_of(branch: str) -> int | None:
    match = _NUMBER.match(branch)
    return int(match[1]) if match else None


@dataclass
class Housekeeping:
    host: RepositoryHost
    workspace: WorkspacePort

    def sweep(self, mine: set[int], claimed: set[int]) -> None:
        """``mine``: issues this manager holds or works; ``claimed``: held by any manager."""
        repository = self.host.repository
        for number, branch in self.workspace.worktrees(repository).items():
            if number in mine:
                continue
            # Open and unclaimed, it may come back here with its agent session: keep it.
            if number in claimed or self.host.issue(number).state == "closed":
                self.workspace.remove(repository, number, branch)
                log.info(
                    "%s#%d: removed the local worktree and branch %s", repository, number, branch
                )
        for branch in self.host.branches(BRANCH_PREFIX):
            held = issue_of(branch)
            if held is None or held in mine or held in claimed:
                continue
            pulls = self.host.pull_requests_for(branch)
            if any(pull.state == "open" for pull in pulls):
                continue
            if pulls or self.host.issue(held).state == "closed":
                self.host.delete_branch(branch)
                log.info("%s#%d: deleted the finished branch %s", repository, held, branch)
