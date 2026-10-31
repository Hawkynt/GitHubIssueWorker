"""Clones, worktrees and branches on the manager's machine (F5).

One cached clone per repository, one ``git worktree`` per issue, and nothing left behind once the
issue's pull request is done. Commits use the maintainer's own identity and signing settings,
copied into the clone because the account home may carry none (F5.4). The clone's push URL is
disabled: agents run without permission prompts, and only the manager pushes, to an explicit URL
and with a lease (F7.2).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from github_issue_worker.adapters.home import tool_environment
from github_issue_worker.ports.agents import JOURNAL_FILE
from github_issue_worker.ports.workspace import CommitInfo

__all__ = ["CommitInfo", "PushRejected", "Workspace", "WorkspaceError"]

_IDENTITY = (
    "user.name",
    "user.email",
    "user.signingkey",
    "gpg.format",
    "gpg.program",
    "gpg.ssh.program",
    "gpg.ssh.allowedsignersfile",
    "commit.gpgsign",
    "tag.gpgsign",
)
_PUSH_DISABLED = "ghiw-push-disabled://only-the-manager-pushes"


class WorkspaceError(Exception):
    pass


class PushRejected(WorkspaceError):
    """The remote branch is not where this worker last left it."""


class Workspace:
    def __init__(self, cache: Path, remote_url: Callable[[str], str], home: Path) -> None:
        self._cache = cache
        self._remote_url = remote_url
        self._home = home

    # --- layout ----------------------------------------------------------------------------------

    def _root(self, repository: str) -> Path:
        owner, name = repository.split("/", 1)
        return self._cache / owner / name

    def worktree_path(self, repository: str, issue: int) -> Path:
        return self._root(repository) / "issues" / str(issue)

    # --- clone -----------------------------------------------------------------------------------

    def clone(self, repository: str) -> Path:
        """The repository's cached clone, created on first use and fetched every time."""
        clone = self._root(repository) / "clone"
        url = self._remote_url(repository)
        if not (clone / ".git").exists():
            identity = self._identity()
            clone.parent.mkdir(parents=True, exist_ok=True)
            self._git(clone.parent, "clone", "--quiet", url, str(clone))
            for key, value in identity.items():
                self._git(clone, "config", key, value)
            self._git(clone, "config", "remote.origin.pushurl", _PUSH_DISABLED)
            exclude = clone / ".git" / "info" / "exclude"
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write("\n.ghiw/\n")
        self._git(clone, "fetch", "--quiet", "--prune", "origin")
        return clone

    def _identity(self) -> dict[str, str]:
        """The maintainer's identity and signing settings, as the manager's git sees them."""
        # Global and system scope only: a plain `git config --get` would read the local config of
        # whatever repository the manager happens to be started in.
        found: dict[str, str] = {}
        for env in (None, tool_environment(self._home)):
            for scope in ("--global", "--system"):
                for key in _IDENTITY:
                    if key in found:
                        continue
                    result = subprocess.run(
                        ["git", "config", scope, "--get", key],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        found[key] = result.stdout.strip()
        if "user.name" not in found or "user.email" not in found:
            raise WorkspaceError(
                "no git identity: set user.name and user.email (git config --global ...)"
            )
        return found

    # --- worktrees -------------------------------------------------------------------------------

    def worktree(self, repository: str, issue: int, branch: str, base: str) -> Path:
        """The issue's worktree on ``branch``: reused when it exists, else created from the remote
        branch when another machine already pushed it, else from ``base``."""
        clone = self.clone(repository)
        path = self.worktree_path(repository, issue)
        if (path / ".git").exists():
            return path

        self._git(clone, "worktree", "prune")
        remote_branch = f"refs/remotes/origin/{branch}"
        start = remote_branch if self._has_ref(clone, remote_branch) else f"origin/{base}"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(clone, "worktree", "add", "--quiet", "-B", branch, str(path), start)
        return path

    def commits(self, worktree: Path, base: str) -> list[CommitInfo]:
        """The commits the branch adds on top of ``base``, oldest first."""
        out = self._git(
            worktree, "log", "--reverse", "--format=%H%x00%B%x1e", f"origin/{base}..HEAD"
        )
        commits = []
        for record in out.split("\x1e"):
            record = record.strip("\n")
            if record:
                sha, _, message = record.partition("\x00")
                commits.append(CommitInfo(sha=sha.strip(), message=message.strip()))
        return commits

    def head(self, worktree: Path) -> str:
        return self._git(worktree, "rev-parse", "HEAD").strip()

    def journal(self, worktree: Path) -> str | None:
        path = worktree / JOURNAL_FILE
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return None

    def rebase(self, worktree: Path, base: str) -> bool:
        """Rebase onto the freshly fetched ``base``. A conflict is aborted and reported as False."""
        self._git(worktree, "fetch", "--quiet", "origin")
        rebased = self._try(worktree, "rebase", "--quiet", f"origin/{base}")
        if rebased.returncode != 0:
            self._try(worktree, "rebase", "--abort")
            return False
        return True

    # --- remote ----------------------------------------------------------------------------------

    def push(self, repository: str, worktree: Path, branch: str, expected: str | None) -> str:
        """Push HEAD to ``branch`` only if the remote branch is still ``expected`` (None: none)."""
        ref = f"refs/heads/{branch}"
        pushed = self._try(
            worktree,
            "push",
            "--quiet",
            "--porcelain",
            f"--force-with-lease={ref}:{expected or ''}",
            self._remote_url(repository),
            f"HEAD:{ref}",
        )
        if pushed.returncode != 0:
            output = pushed.stdout + pushed.stderr
            if "stale info" in output or "[rejected]" in output:
                raise PushRejected(f"{branch} changed on the remote: {output.strip()}")
            raise WorkspaceError(f"pushing {branch} failed: {output.strip()}")
        return self.head(worktree)

    def remote_head(self, repository: str, branch: str) -> str | None:
        out = self._git(
            self._cache, "ls-remote", self._remote_url(repository), f"refs/heads/{branch}"
        )
        return out.split()[0] if out.strip() else None

    # --- clean-up --------------------------------------------------------------------------------

    def remove(self, repository: str, issue: int, branch: str) -> None:
        """Clean state after a pull request (F5.5): no worktree, no local branch."""
        clone = self._root(repository) / "clone"
        if not clone.exists():
            return
        path = self.worktree_path(repository, issue)
        self._try(clone, "worktree", "remove", "--force", str(path))
        self._git(clone, "worktree", "prune")
        self._try(clone, "branch", "-D", branch)
        # Branches an agent made on the side (backups before a rebase, ...) are left over once
        # their worktree is gone; everything of value was pushed. A checked-out branch stays.
        checked_out = set(self._checked_out(clone).values())
        for name in self._git(clone, "branch", "--format=%(refname:short)").split():
            if name not in checked_out:
                self._try(clone, "branch", "-D", name)

    def worktrees(self, repository: str) -> dict[int, str]:
        clone = self._root(repository) / "clone"
        if not clone.exists():
            return {}
        issues = (self._root(repository) / "issues").resolve()
        found = {}
        for path, branch in self._checked_out(clone).items():
            if path.parent == issues and path.name.isdigit():
                found[int(path.name)] = branch
        return found

    def _checked_out(self, clone: Path) -> dict[Path, str]:
        """Every worktree of the clone, the clone itself included, with its branch."""
        found: dict[Path, str] = {}
        path: Path | None = None
        for line in self._git(clone, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ")).resolve()
            elif line.startswith("branch ") and path is not None:
                found[path] = line.removeprefix("branch refs/heads/")
        return found

    # --- git ----------------------------------------------------------------------------------

    def _has_ref(self, cwd: Path, ref: str) -> bool:
        return self._try(cwd, "rev-parse", "--verify", "--quiet", ref).returncode == 0

    def _try(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        cwd.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env={**tool_environment(self._home), "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
            check=False,
        )

    def _git(self, cwd: Path, *args: str) -> str:
        result = self._try(cwd, *args)
        if result.returncode != 0:
            raise WorkspaceError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result.stdout
