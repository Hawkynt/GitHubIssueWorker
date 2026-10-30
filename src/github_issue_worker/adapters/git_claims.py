"""Claims stored as hidden git refs, changed only by compare-and-swap pushes (F11.1).

Each claim lives at ``refs/ghiw/claims/<issue>`` on the remote and points at a commit whose tree
holds ``claim.json``. Every change is a new commit whose parent is the version it replaces, so the
ref's history is the claim's history. Writes are ``git push --force-with-lease=<ref>:<expected>``:
the remote applies them only if the ref still holds the expected value. Measured against
github.com, of six concurrent creates exactly one succeeded. The REST ref-update endpoint does not
enforce this and is never used for claims.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from github_issue_worker.adapters.home import account_home, tool_environment
from github_issue_worker.domain.claim import Claim
from github_issue_worker.ports.claims import ClaimConflict, ClaimStoreError, StoredClaim

_CLAIMS = "refs/ghiw/claims"
_SEEN = "refs/ghiw-seen/claims"
_FILE = "claim.json"
_REJECTED = ("[rejected]", "stale info", "already exists", "fetch first")


class GitClaimStore:
    """A `ClaimStore` over a local clone of the watched repository and its remote."""

    def __init__(self, clone: Path, remote: str = "origin", home: Path | None = None) -> None:
        self._clone = clone
        self._remote = remote
        self._home = home or account_home()

    def read(self, issue: int) -> StoredClaim | None:
        seen = f"{_SEEN}/{issue}"
        fetched = self._run(
            "fetch", "--quiet", "--no-tags", self._remote, f"+{_CLAIMS}/{issue}:{seen}", check=False
        )
        if fetched.returncode != 0:
            if "couldn't find remote ref" in fetched.stderr:
                self._run("update-ref", "-d", seen, check=False)
                return None
            raise ClaimStoreError(f"reading the claim of issue {issue}: {fetched.stderr.strip()}")

        version = self._run("rev-parse", seen).stdout.strip()
        content = self._run("cat-file", "blob", f"{version}:{_FILE}").stdout
        return StoredClaim(version, Claim.from_json(json.loads(content)))

    def create(self, claim: Claim) -> str:
        commit = self._commit(claim, parent=None)
        self._push(claim.issue, f"{commit}:{_CLAIMS}/{claim.issue}", expected="")
        return commit

    def replace(self, expected: str, claim: Claim) -> str:
        commit = self._commit(claim, parent=expected)
        self._push(claim.issue, f"{commit}:{_CLAIMS}/{claim.issue}", expected=expected)
        return commit

    def delete(self, issue: int, expected: str) -> None:
        self._push(issue, f":{_CLAIMS}/{issue}", expected=expected)
        self._run("update-ref", "-d", f"{_SEEN}/{issue}", check=False)

    def _commit(self, claim: Claim, parent: str | None) -> str:
        content = json.dumps(claim.to_json(), indent=2, sort_keys=True) + "\n"
        blob = self._run("hash-object", "-w", "--stdin", stdin=content).stdout.strip()
        tree = self._run("mktree", stdin=f"100644 blob {blob}\t{_FILE}\n").stdout.strip()
        message = f"claim #{claim.issue} gen {claim.generation} {claim.state} by {claim.holder}"
        parents = ["-p", parent] if parent else []
        # Claim commits are machine records outside refs/heads: unsigned, and authored by the
        # holder rather than by a person.
        identity = str(claim.holder)
        email = f"{claim.holder.manager}@ghiw.invalid"
        env = {
            "GIT_AUTHOR_NAME": identity,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": identity,
            "GIT_COMMITTER_EMAIL": email,
        }
        args = ["commit-tree", "--no-gpg-sign", tree, *parents, "-m", message]
        return self._run(*args, env=env).stdout.strip()

    def _push(self, issue: int, refspec: str, expected: str) -> None:
        ref = f"{_CLAIMS}/{issue}"
        pushed = self._run(
            "push",
            "--quiet",
            "--porcelain",
            f"--force-with-lease={ref}:{expected}",
            self._remote,
            refspec,
            check=False,
        )
        if pushed.returncode == 0:
            return
        output = pushed.stdout + pushed.stderr
        if any(marker in output for marker in _REJECTED):
            raise ClaimConflict(f"the claim of issue {issue} changed: {output.strip()}")
        raise ClaimStoreError(f"writing the claim of issue {issue}: {output.strip()}")

    def _run(
        self,
        *args: str,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self._clone,
            input=stdin,
            capture_output=True,
            text=True,
            # Messages are parsed, so they must be git's English ones whatever the user's locale.
            env={
                **tool_environment(self._home),
                "LC_ALL": "C",
                "LANGUAGE": "C",
                "GIT_TERMINAL_PROMPT": "0",
                **(env or {}),
            },
            check=False,
        )
        if check and result.returncode != 0:
            raise ClaimStoreError(f"git {args[0]} failed: {result.stderr.strip()}")
        return result
