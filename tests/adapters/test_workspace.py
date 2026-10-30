from __future__ import annotations

import subprocess
from pathlib import Path

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
