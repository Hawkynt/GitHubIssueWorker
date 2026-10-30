"""How a worker finds its agent (F15.1 to F15.4).

``path`` is every normal installation and needs nothing else. The presets resolve portable layouts
directly instead of through their interactive launchers, whose menus, first-run installers and
banners would end up in the agent's output:

- ClaudeCodePortable keeps each tool in a profile: ``npm-global/bin/{claude,codex}`` with
  ``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` pointing into the profile, and ``.local/bin/agy`` with the
  profile as ``HOME``. The npm-installed CLIs are node scripts, so the portable node goes first on
  ``PATH``.
- ClaudeLocal's ``claude.sh`` is already non-interactive with ``--prompt``.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from github_issue_worker.configuration import (
    ClaudeCodePortableInstallation,
    ClaudeLocalInstallation,
    ExecutableInstallation,
    WorkerSettings,
)

EXECUTABLES = {"claude": "claude", "codex": "codex", "antigravity": "agy", "opencode": "opencode"}


class Unavailable(Exception):
    """The worker's agent cannot be started here. The message says why."""


@dataclass(frozen=True, slots=True)
class Resolved:
    executable: Path
    env: dict[str, str] = field(default_factory=dict)
    local: bool = False
    """ClaudeLocal's launcher rather than the agent itself."""


def _windows() -> bool:
    return sys.platform == "win32"


def _expand(path: str, home: Path | None) -> Path:
    """``~`` means the account home: a relocated ``HOME`` would point into a launcher's profile."""
    if home is not None and (path == "~" or path.startswith(("~/", "~\\"))):
        return home / path[2:]
    return Path(path).expanduser()


def resolve(worker: WorkerSettings, home: Path | None = None) -> Resolved:
    installation = worker.installation
    if isinstance(installation, ExecutableInstallation):
        path = _expand(installation.path, home)
        if not path.exists():
            raise Unavailable(f"{worker.name}: {path} does not exist")
        return Resolved(path, dict(installation.env))
    if isinstance(installation, ClaudeCodePortableInstallation):
        return _portable(worker, installation, home)
    if isinstance(installation, ClaudeLocalInstallation):
        root = _expand(installation.root, home)
        launcher = root / ("Claude.bat" if _windows() else "claude.sh")
        if not launcher.exists():
            raise Unavailable(f"{worker.name}: no ClaudeLocal launcher at {launcher}")
        return Resolved(launcher, local=True)

    name = EXECUTABLES.get(worker.agent)
    found = shutil.which(name) if name else None
    if not found:
        raise Unavailable(f"{worker.name}: `{name or worker.agent}` is not on PATH")
    return Resolved(Path(found))


def _portable(
    worker: WorkerSettings, installation: ClaudeCodePortableInstallation, home: Path | None
) -> Resolved:
    root = _expand(installation.root, home)
    profile = root / "profiles" / installation.profile
    if not profile.is_dir():
        raise Unavailable(f"{worker.name}: no ClaudeCodePortable profile {profile}")

    suffix = ".cmd" if _windows() else ""
    if worker.agent == "claude":
        tool, env = (
            profile / "npm-global" / "bin" / f"claude{suffix}",
            {"CLAUDE_CONFIG_DIR": str(profile / "claude-config")},
        )
    elif worker.agent == "codex":
        tool, env = (
            profile / "npm-global" / "bin" / f"codex{suffix}",
            {"CODEX_HOME": str(profile / "codex-config")},
        )
    elif worker.agent == "antigravity":
        tool = profile / ".local" / "bin" / ("agy.exe" if _windows() else "agy")
        env = {"HOME": str(profile)}
        if _windows():
            env |= {"USERPROFILE": str(profile), "LOCALAPPDATA": str(profile / "AppData" / "Local")}
    else:
        raise Unavailable(f"{worker.name}: ClaudeCodePortable has no {worker.agent}")

    if not tool.exists():
        raise Unavailable(
            f"{worker.name}: {tool} is not installed in profile {installation.profile}"
        )

    nodes = sorted((root / "app" / "node").glob("node-*/bin")) or sorted(
        (root / "app" / "node").glob("node-*")
    )
    if nodes:
        env["PATH"] = os.pathsep.join([str(nodes[-1]), os.environ.get("PATH", "")])
    return Resolved(tool, env)
