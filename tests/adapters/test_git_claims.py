from __future__ import annotations

import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from github_issue_worker.adapters.git_claims import GitClaimStore
from github_issue_worker.application.coordinator import Coordinator
from github_issue_worker.domain.claim import Claim, ClaimPolicy, Holder
from github_issue_worker.ports.claims import ClaimConflict, ClaimStoreError
from tests.fakes import FakeClock

pytestmark = pytest.mark.external_interop

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
POLICY = ClaimPolicy(validity=timedelta(days=7), grace=timedelta(days=1))
CODEX = Holder(manager="office-pc", worker="codex")
CLAUDE = Holder(manager="laptop", worker="claude")


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    return bare


def clone(remote: Path, where: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(where)], check=True, capture_output=True)
    # A signing requirement in the user's config must not break claim commits.
    git("config", "commit.gpgsign", "true", cwd=where)
    git("config", "gpg.program", "false", cwd=where)
    return where


@pytest.fixture
def store(remote: Path, tmp_path: Path) -> GitClaimStore:
    return GitClaimStore(clone(remote, tmp_path / "a"))


def claim(issue: int = 42, holder: Holder = CODEX) -> Claim:
    return POLICY.new_claim(issue=issue, holder=holder, now=T0, branch=f"issue/{issue}-x")


def test_reading_an_absent_claim_gives_none(store: GitClaimStore) -> None:
    assert store.read(42) is None


def test_issues_lists_every_claimed_issue(store: GitClaimStore) -> None:
    assert store.issues() == []
    store.create(claim(42))
    store.create(claim(7))
    assert store.issues() == [7, 42]


def test_a_created_claim_reads_back(store: GitClaimStore, remote: Path) -> None:
    version = store.create(claim())
    stored = store.read(42)
    assert stored is not None
    assert stored.version == version
    assert stored.claim == claim()
    assert git("rev-parse", "refs/ghiw/claims/42", cwd=remote) == version


def test_creating_an_existing_claim_conflicts(store: GitClaimStore) -> None:
    store.create(claim())
    with pytest.raises(ClaimConflict):
        store.create(claim(holder=CLAUDE))


def test_replace_needs_the_current_version(store: GitClaimStore, remote: Path) -> None:
    first = store.create(claim())
    second = store.replace(first, claim().replace(step="implement"))
    with pytest.raises(ClaimConflict):
        store.replace(first, claim().replace(step="push"))

    stored = store.read(42)
    assert stored is not None and stored.claim.step == "implement"
    # Every change is a commit on top of the previous one: the claim's history.
    assert git("rev-parse", f"{second}^", cwd=remote) == first


def test_delete_needs_the_current_version(store: GitClaimStore) -> None:
    first = store.create(claim())
    second = store.replace(first, claim().replace(step="done"))
    with pytest.raises(ClaimConflict):
        store.delete(42, first)
    store.delete(42, second)
    assert store.read(42) is None


def test_a_second_store_sees_the_first_ones_writes(remote: Path, tmp_path: Path) -> None:
    a = GitClaimStore(clone(remote, tmp_path / "a"))
    b = GitClaimStore(clone(remote, tmp_path / "b"))
    version = a.create(claim())
    stored = b.read(42)
    assert stored is not None and stored.version == version
    a.replace(version, claim().replace(step="push"))
    with pytest.raises(ClaimConflict):
        b.replace(version, claim().replace(step="merge"))


def test_exactly_one_of_many_concurrent_claimants_wins(remote: Path, tmp_path: Path) -> None:
    stores = [GitClaimStore(clone(remote, tmp_path / f"c{i}")) for i in range(6)]
    winners: list[int] = []
    barrier = threading.Barrier(len(stores))

    def attempt(index: int) -> None:
        barrier.wait()
        try:
            stores[index].create(claim(holder=Holder(manager=f"m{index}", worker="w")))
            winners.append(index)
        except ClaimConflict:
            pass

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(len(stores))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1
    stored = stores[0].read(42)
    assert stored is not None and stored.claim.holder.manager == f"m{winners[0]}"


def test_claims_are_invisible_to_an_ordinary_clone(
    store: GitClaimStore, remote: Path, tmp_path: Path
) -> None:
    store.create(claim())
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", "-q", str(remote), str(fresh)], check=True, capture_output=True)
    assert "ghiw" not in git("for-each-ref", cwd=fresh)


def test_the_coordinator_works_over_git(remote: Path, tmp_path: Path) -> None:
    clock = FakeClock(T0)
    office = Coordinator(
        store=GitClaimStore(clone(remote, tmp_path / "a")), clock=clock, policy=POLICY
    )
    laptop = Coordinator(
        store=GitClaimStore(clone(remote, tmp_path / "b")), clock=clock, policy=POLICY
    )

    handle = office.claim(42, CODEX, branch="issue/42-x")
    assert handle is not None
    assert laptop.claim(42, CLAUDE, branch="issue/42-x") is None

    clock.advance(timedelta(days=8))
    taken = laptop.claim(42, CLAUDE, branch="issue/42-x")
    assert taken is not None and taken.claim.takeovers == 1


def test_network_git_runs_with_the_account_home(remote: Path, tmp_path: Path) -> None:
    # Only the account home's .gitconfig knows the "ghiw-test:" alias for the remote, so the claim
    # can be written and read back only if that home is the one git runs with.
    account = tmp_path / "account-home"
    account.mkdir()
    (account / ".gitconfig").write_text(
        # as_posix: backslashes in a Windows path are escapes in a .gitconfig value
        f'[url "{remote.as_posix()}"]\n\tinsteadOf = ghiw-test:\n',
        encoding="utf-8",
    )
    work = clone(remote, tmp_path / "w")
    git("remote", "set-url", "origin", "ghiw-test:", cwd=work)

    store = GitClaimStore(work, home=account)
    version = store.create(claim())
    stored = store.read(42)
    assert stored is not None and stored.version == version

    with pytest.raises(ClaimStoreError):
        GitClaimStore(work, home=tmp_path / "elsewhere").read(42)


def test_claims_are_pushed_to_the_explicit_url_when_the_push_url_is_disabled(
    remote: Path, tmp_path: Path
) -> None:
    # The workspace disables the clone's push URL so agents cannot push; claims must still work.
    work = clone(remote, tmp_path / "w")
    git("config", "remote.origin.pushurl", "ghiw-push-disabled://only-the-manager-pushes", cwd=work)
    with pytest.raises(ClaimStoreError):
        GitClaimStore(work).create(claim())

    store = GitClaimStore(work, push_url=str(remote))
    version = store.create(claim())
    stored = store.read(42)
    assert stored is not None and stored.version == version
    store.delete(42, version)
    assert store.read(42) is None


def test_the_claim_history_is_read_from_the_refs_commits(store: GitClaimStore) -> None:
    # F14.6: every version of a claim is a commit, so its history is the ref's commit chain.
    first = claim()
    version = store.create(first)
    renewed = POLICY.renewed(first, T0 + timedelta(hours=1))
    version = store.replace(version, renewed)
    stepped = POLICY.renewed(renewed, T0 + timedelta(hours=2), step="implement")
    store.replace(version, stepped)
    assert store.history(42, 10) == [first, renewed, stepped]
    assert store.history(42, 2) == [renewed, stepped]
    assert store.history(7, 10) == []
