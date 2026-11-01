from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_issue_worker.application.coordinator import ClaimLost, Coordinator
from github_issue_worker.application.manager import RepositoryWatch
from github_issue_worker.application.repository_removal import release_repository
from github_issue_worker.domain.claim import ClaimPolicy, ClaimState, Holder
from tests.application.run_fakes import FakeHost, FakeWorkspace
from tests.fakes import FakeClock, InMemoryClaimStore

NOW = datetime(2026, 10, 31, tzinfo=UTC)


def setup_watch(tmp_path: Path) -> tuple[RepositoryWatch, InMemoryClaimStore]:
    store = InMemoryClaimStore()
    coordinator = Coordinator(
        store, FakeClock(NOW), ClaimPolicy(timedelta(days=7), timedelta(days=1))
    )
    for number, holder in [(1, "here"), (2, "here"), (3, "away"), (4, "here")]:
        handle = coordinator.claim(number, Holder(holder, f"w{number}"), f"issue/{number}")
        assert handle is not None
        if number == 2:
            coordinator.pause(handle, NOW + timedelta(hours=1))
        if number == 4:
            coordinator.complete(handle)
    return RepositoryWatch("o/r", FakeHost(), coordinator, store), store


def test_remove_checkpoints_then_releases_working_and_paused_local_claims_only(
    tmp_path: Path,
) -> None:
    watch, store = setup_watch(tmp_path)
    before = {n: store.read(n) for n in (3, 4)}
    snapshots = []
    workspace = FakeWorkspace(tmp_path)

    def checkpoint(repo: str, number: int, branch: str, base: str) -> str:
        assert store.current(number).state.holds
        snapshots.append(number)
        return "snapshot"

    workspace.checkpoint = checkpoint
    release_repository(watch, "here", workspace, "main")
    assert snapshots == [1, 2]
    assert all(store.current(n).state is ClaimState.RELEASED for n in (1, 2))
    assert {n: store.read(n) for n in (3, 4)} == before
    release_repository(watch, "here", workspace, "main")
    assert snapshots == [1, 2]


def test_checkpoint_failure_keeps_claim_and_recoverable_work_for_retry(tmp_path: Path) -> None:
    watch, store = setup_watch(tmp_path)
    workspace = FakeWorkspace(tmp_path)

    def fail(*args: object) -> None:
        raise OSError("checkpoint publication failed")

    workspace.checkpoint = fail
    with pytest.raises(OSError, match="publication failed"):
        release_repository(watch, "here", workspace, "main")
    assert store.current(1).state is ClaimState.WORKING
    assert store.current(2).state is ClaimState.PAUSED


def test_concurrent_claim_change_is_never_overwritten_during_removal(tmp_path: Path) -> None:
    watch, store = setup_watch(tmp_path)
    workspace = FakeWorkspace(tmp_path)

    def checkpoint(*args: object) -> str:
        store.force_create(store.current(1).replace(holder=Holder("new-holder", "worker")))
        return "snapshot"

    workspace.checkpoint = checkpoint
    with pytest.raises(ClaimLost):
        release_repository(watch, "here", workspace, "main")
    assert store.current(1).holder.manager == "new-holder"
    assert store.current(1).state.holds


def test_partial_removal_is_retryable_without_repeating_completed_releases(tmp_path: Path) -> None:
    watch, store = setup_watch(tmp_path)
    workspace = FakeWorkspace(tmp_path)
    snapshots = []

    def checkpoint(repo: str, number: int, branch: str, base: str) -> str:
        snapshots.append(number)
        if number == 2:
            raise OSError("second checkpoint failed")
        return "saved"

    workspace.checkpoint = checkpoint
    with pytest.raises(OSError, match="second checkpoint"):
        release_repository(watch, "here", workspace, "main")
    assert store.current(1).state is ClaimState.RELEASED
    assert store.current(2).state is ClaimState.PAUSED
    workspace.checkpoint = lambda repo, number, branch, base: snapshots.append(number) or "saved"
    release_repository(watch, "here", workspace, "main")
    assert snapshots == [1, 2, 2]
    assert store.current(2).state is ClaimState.RELEASED


def test_release_conflict_after_checkpoint_preserves_the_new_holder(tmp_path: Path) -> None:
    watch, store = setup_watch(tmp_path)
    workspace = FakeWorkspace(tmp_path)

    def concurrent_change(number: int, label: str | None) -> None:
        store.force_create(store.current(number).replace(holder=Holder("another", "w")))

    watch.host.set_worker_label = concurrent_change
    with pytest.raises(ClaimLost):
        release_repository(watch, "here", workspace, "main")
    assert store.current(1).holder.manager == "another" and store.current(1).state.holds
