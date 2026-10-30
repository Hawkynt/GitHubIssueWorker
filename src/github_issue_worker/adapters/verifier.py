"""Independent verification of an agent's work: the repository's own build and tests (F6.6).

It runs code the agent wrote, so it runs in the agent's scrubbed environment, never with the
manager's GitHub access. Tests that need GitHub belong to CI.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from github_issue_worker.adapters.process import run_invocation
from github_issue_worker.ports.agents import Invocation

_DETECTED = (
    ("*.slnx", "dotnet test"),
    ("*.sln", "dotnet test"),
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("pyproject.toml", "python -m pytest -q"),
    ("setup.py", "python -m pytest -q"),
    ("go.mod", "go test ./..."),
)


def detect_commands(worktree: Path) -> list[str]:
    for pattern, command in _DETECTED:
        if any(worktree.glob(pattern)):
            return [command]
    return []


def _invocation(command: str, worktree: Path) -> Invocation:
    # On Windows the command line goes to cmd verbatim: building ["cmd", "/c", command] lets
    # Python quote it with \" escapes cmd does not understand, which broke quoted paths.
    if sys.platform == "win32":
        return Invocation(argv=[command], cwd=worktree, shell=True)
    return Invocation(argv=["bash", "-c", command], cwd=worktree)


@dataclass(frozen=True, slots=True)
class Verifier:
    build: str | None = None
    test: str | None = None
    timeout: float = 3600

    def commands(self, worktree: Path) -> list[str]:
        configured = [command for command in (self.build, self.test) if command]
        return configured or detect_commands(worktree)

    def verify(self, worktree: Path) -> tuple[bool, str]:
        commands = self.commands(worktree)
        if not commands:
            return True, "no build or test command configured or detected"
        output = []
        for command in commands:
            result = run_invocation(_invocation(command, worktree), self.timeout)
            output.append(f"$ {command}\n{result.stdout}{result.stderr}")
            if result.timed_out:
                output.append(f"(timed out after {self.timeout:.0f} s)")
                return False, "\n".join(output)
            if result.exit_code != 0:
                return False, "\n".join(output)
        return True, "\n".join(output)
