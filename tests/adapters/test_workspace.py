from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from github_issue_worker.adapters.workspace import PushRejected, Workspace, WorkspaceError

pytestmark = pytest.mark.external_interop

REPO = "Hawkynt/ShiftDiff"


def git(*args: str, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)
    return result.stdout.strip()


def commit(cwd: Path, message: str, name: str = "file.txt") -> str:
    (cwd / name).write_text(message, encoding="utf-8")
    git("add", "-A", cwd=cwd)
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", message, cwd=cwd)
    return git("rev-parse", "HEAD", cwd=cwd)


@pytest.fixture
def identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Jane Doe\n\temail = jane@example.org\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    return config


@pytest.fixture
def remote(tmp_path: Path, identity: Path) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    commit(seed, "+ initial")
    git("push", "-q", "origin", "HEAD:main", cwd=seed)
    return bare


@pytest.fixture
def workspace(tmp_path: Path, remote: Path) -> Workspace:
    return Workspace(
        cache=tmp_path / "cache", remote_url=lambda repo: str(remote), home=Path.home()
    )


def test_a_worktree_is_created_on_a_fresh_branch_from_the_base(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, issue=42, branch="issue/42-retry", base="main")
    assert git("branch", "--show-current", cwd=tree) == "issue/42-retry"
    assert (tree / "file.txt").read_text(encoding="utf-8") == "+ initial"
    assert git("config", "user.name", cwd=tree) == "Jane Doe"
    assert git("config", "user.email", cwd=tree) == "jane@example.org"


def test_the_worktree_is_reused_when_the_issue_continues(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "* local work")
    again = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    assert again == tree
    assert "* local work" in git("log", "--format=%s", cwd=again)


def test_manager_files_are_never_committed(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    (tree / ".ghiw").mkdir()
    (tree / ".ghiw" / "outcome.json").write_text("{}", encoding="utf-8")
    assert git("status", "--porcelain", cwd=tree) == ""


def test_a_plain_git_push_from_the_worktree_fails(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "* sneaky")
    pushed = subprocess.run(["git", "push", "origin", "HEAD"], cwd=tree, capture_output=True)
    assert pushed.returncode != 0


def test_commits_lists_what_the_branch_adds(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "+ first\n\nbody one", "a.txt")
    commit(tree, "* second\n\nbody two", "b.txt")
    commits = workspace.commits(tree, base="main")
    assert [c.message.split("\n")[0] for c in commits] == ["+ first", "* second"]


def test_the_fork_point_is_the_base_commit_the_branch_started_from(
    workspace: Workspace, remote: Path
) -> None:
    started = git("rev-parse", "refs/heads/main", cwd=remote)
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "+ work\n\nbody", "a.txt")
    assert workspace.fork_point(tree, "main") == started


def test_the_manager_pushes_with_a_lease(workspace: Workspace, remote: Path) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    first = commit(tree, "+ one\n\nbody", "a.txt")
    workspace.push(REPO, tree, "issue/42-retry", expected=None)
    assert git("rev-parse", "refs/heads/issue/42-retry", cwd=remote) == first

    second = commit(tree, "+ two\n\nbody", "b.txt")
    workspace.push(REPO, tree, "issue/42-retry", expected=first)
    assert workspace.remote_head(REPO, "issue/42-retry") == second

    commit(tree, "+ three\n\nbody", "c.txt")
    with pytest.raises(PushRejected):  # someone else's commit is on the branch now
        workspace.push(REPO, tree, "issue/42-retry", expected=first)


def test_remove_leaves_a_clean_state(workspace: Workspace, tmp_path: Path) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "+ work\n\nbody", "a.txt")
    workspace.remove(REPO, 42, "issue/42-retry")

    clone = workspace.clone(REPO)
    assert not tree.exists()
    assert git("worktree", "list", "--porcelain", cwd=clone).count("worktree ") == 1
    assert git("branch", "--format=%(refname:short)", cwd=clone) == "main"


def test_rebase_onto_a_moved_base(workspace: Workspace, remote: Path, tmp_path: Path) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "+ mine\n\nbody", "mine.txt")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True)
    commit(other, "+ theirs", "theirs.txt")
    git("push", "-q", "origin", "HEAD:main", cwd=other)

    assert workspace.rebase(tree, base="main") is True
    assert (tree / "theirs.txt").exists() and (tree / "mine.txt").exists()


def test_a_conflicting_rebase_is_abandoned_cleanly(
    workspace: Workspace, remote: Path, tmp_path: Path
) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    commit(tree, "+ mine\n\nbody", "file.txt")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True, capture_output=True)
    commit(other, "+ theirs", "file.txt")
    git("push", "-q", "origin", "HEAD:main", cwd=other)

    assert workspace.rebase(tree, base="main") is False
    assert git("status", "--porcelain", cwd=tree) == ""
    assert (tree / "file.txt").read_text(encoding="utf-8") == "+ mine\n\nbody"


def test_no_identity_anywhere_is_an_error(
    tmp_path: Path, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    workspace = Workspace(cache=tmp_path / "c2", remote_url=lambda r: str(remote), home=tmp_path)
    with pytest.raises(WorkspaceError, match=r"user\.name"):
        workspace.clone(REPO)


def test_remove_also_drops_branches_an_agent_left_behind(workspace: Workspace) -> None:
    kept = workspace.worktree(REPO, issue=7, branch="issue/7-kept", base="main")
    gone = workspace.worktree(REPO, issue=8, branch="issue/8-gone", base="main")
    git("branch", "backup/issue8-before-rebase", cwd=gone)
    workspace.remove(REPO, 8, "issue/8-gone")
    branches = git("branch", "--format=%(refname:short)", cwd=kept).split()
    assert "issue/8-gone" not in branches and "backup/issue8-before-rebase" not in branches
    assert "issue/7-kept" in branches  # still checked out in its worktree
    assert workspace.worktrees(REPO) == {7: "issue/7-kept"}


def test_the_journal_is_read_from_the_worktree_and_is_none_when_missing(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "cache", lambda repository: "", tmp_path)
    assert workspace.journal(tmp_path) is None
    (tmp_path / ".ghiw").mkdir()
    (tmp_path / ".ghiw" / "journal.md").write_text("## Done\n- a\n", encoding="utf-8")
    assert workspace.journal(tmp_path) == "## Done\n- a\n"


def test_removal_checkpoint_restores_commits_dirty_files_and_journal_on_another_machine(
    workspace: Workspace, remote: Path, tmp_path: Path
) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42-retry", "main")
    head = commit(tree, "+ committed work", "committed.txt")
    (tree / "file.txt").write_text("uncommitted change\n", encoding="utf-8")
    (tree / "new.bin").write_bytes(bytes(range(256)))
    (tree / ".ghiw").mkdir()
    (tree / ".ghiw" / "journal.md").write_text(
        "Continue the remaining scenarios.\n", encoding="utf-8"
    )
    (tree / ".ghiw" / "prompt.md").write_text("transient input", encoding="utf-8")
    status = git("status", "--porcelain", cwd=tree)
    checkpoint = workspace.checkpoint(REPO, 42, "issue/42-retry", "main")
    assert git("rev-parse", f"{checkpoint}^", cwd=remote) == head
    assert git("rev-parse", "HEAD", cwd=tree) == head
    assert git("status", "--porcelain", cwd=tree) == status
    assert workspace.remote_head(REPO, "issue/42-retry") is None

    other = Workspace(tmp_path / "other-machine", lambda repo: str(remote), Path.home())
    restored = other.worktree(REPO, 42, "issue/42-retry", "main")
    assert git("rev-parse", "HEAD", cwd=restored) == head
    assert (restored / "committed.txt").exists()
    assert (restored / "file.txt").read_text(encoding="utf-8") == "uncommitted change\n"
    assert (restored / "new.bin").read_bytes() == bytes(range(256))
    assert (restored / ".ghiw" / "journal.md").read_text(
        encoding="utf-8"
    ) == "Continue the remaining scenarios.\n"
    assert not (restored / ".ghiw" / "prompt.md").exists()
    assert "file.txt" in git("diff", "--name-only", cwd=restored)
    assert "?? new.bin" in git("status", "--porcelain", cwd=restored)


def test_checkpoint_preserves_deletions_and_does_not_modify_the_index(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    (tree / "staged.txt").write_text("staged", encoding="utf-8")
    git("add", "staged.txt", cwd=tree)
    (tree / "file.txt").unlink()
    before = git("status", "--porcelain", cwd=tree)
    workspace.checkpoint(REPO, 9, "issue/9", "main")
    assert git("status", "--porcelain", cwd=tree) == before


def test_checkpoint_retries_reuse_remote_checkpoint_when_local_worktree_is_missing(
    workspace: Workspace, remote: Path
) -> None:
    tree = workspace.worktree(REPO, 42, "issue/42", "main")
    commit(tree, "+ saved")
    saved = workspace.checkpoint(REPO, 42, "issue/42", "main")
    workspace.remove(REPO, 42, "issue/42")
    assert workspace.checkpoint(REPO, 42, "issue/42", "main") == saved


def test_checkpoint_restores_deleted_files_as_uncommitted_deletions(
    workspace: Workspace, remote: Path, tmp_path: Path
) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    (tree / "file.txt").unlink()
    workspace.checkpoint(REPO, 9, "issue/9", "main")
    other = Workspace(tmp_path / "other-deletion", lambda repo: str(remote), Path.home())
    restored = other.worktree(REPO, 9, "issue/9", "main")
    assert not (restored / "file.txt").exists()
    assert (
        " D file.txt" in git("status", "--porcelain", cwd=restored)
        or git("status", "--porcelain", cwd=restored) == "D file.txt"
    )
    assert git("show", "HEAD:file.txt", cwd=restored) == "+ initial"


def test_checkpoint_publication_rejects_a_concurrent_wip_change_without_losing_local_work(
    workspace: Workspace, remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    workspace.checkpoint(REPO, 9, "issue/9", "main")
    (tree / "new.txt").write_text("keep me", encoding="utf-8")
    original = workspace._git

    def race(cwd: Path, *args: str, **kwargs: Any) -> str:
        if args[0] == "push":
            git("update-ref", "refs/ghiw/wip/9", "refs/heads/main", cwd=remote)
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(workspace, "_git", race)
    with pytest.raises(WorkspaceError):
        workspace.checkpoint(REPO, 9, "issue/9", "main")
    assert (tree / "new.txt").read_text(encoding="utf-8") == "keep me"
    assert git("rev-parse", "refs/ghiw/wip/9", cwd=remote) == git("rev-parse", "main", cwd=remote)


def test_fresh_run_does_not_restore_a_prior_runs_checkpoint(workspace: Workspace) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    (tree / "old.txt").write_text("old work", encoding="utf-8")
    workspace.checkpoint(REPO, 9, "issue/9", "main")
    workspace.remove(REPO, 9, "issue/9")
    fresh = workspace.worktree(REPO, 9, "issue/9", "main", resume=False)
    assert not (fresh / "old.txt").exists()


def test_removal_checkpoint_preserves_local_branch_commits_when_worktree_was_removed(
    workspace: Workspace, remote: Path
) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    head = commit(tree, "+ unpushed work")
    clone = workspace.clone(REPO)
    git("worktree", "remove", str(tree), cwd=clone)
    saved = workspace.checkpoint(REPO, 9, "issue/9", "main")
    assert git("rev-parse", f"{saved}^", cwd=remote) == head


def test_checkpoint_includes_force_staged_ignored_source_without_changing_the_index(
    workspace: Workspace, remote: Path, tmp_path: Path
) -> None:
    tree = workspace.worktree(REPO, 9, "issue/9", "main")
    (tree / ".gitignore").write_text("ignored-source.txt\n", encoding="utf-8")
    (tree / "ignored-source.txt").write_text("intentionally staged", encoding="utf-8")
    git("add", "-f", "ignored-source.txt", cwd=tree)
    before = git("diff", "--cached", cwd=tree)
    workspace.checkpoint(REPO, 9, "issue/9", "main")
    other = Workspace(tmp_path / "other-staging", lambda repo: str(remote), Path.home())
    restored = other.worktree(REPO, 9, "issue/9", "main")
    assert (restored / "ignored-source.txt").read_text(encoding="utf-8") == "intentionally staged"
    assert git("diff", "--cached", cwd=tree) == before
