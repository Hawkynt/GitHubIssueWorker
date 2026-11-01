"""Checkpoint and release this manager's claims before unwatching a repository (F1.4)."""

from __future__ import annotations

from github_issue_worker.application.coordinator import ClaimHandle
from github_issue_worker.application.issue_run import checkpoint_and_release
from github_issue_worker.application.manager import RepositoryWatch
from github_issue_worker.ports.workspace import WorkspacePort


def release_repository(
    watch: RepositoryWatch, manager_id: str, workspace: WorkspacePort, base: str
) -> None:
    """Called only after local runs have stopped. Partial completion is safe to retry."""
    for number in watch.store.issues():
        stored = watch.store.read(number)
        if stored is None or not stored.claim.state.holds:
            continue
        claim = stored.claim
        if claim.holder.manager != manager_id:
            continue
        checkpoint_and_release(
            watch.host,
            workspace,
            watch.coordinator,
            ClaimHandle(stored.version, claim),
            watch.name,
            base,
        )
