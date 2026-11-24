"""Finds agent installations and proves each one answers (F15.3, F15.9, F14.4).

Proposes one worker per installation: ClaudeCodePortable profiles under the default, the configured
and the current session's root, ClaudeLocal, and the agents on PATH. Each carries the ``version``
its installation answered with, or the ``problem`` why it cannot be added.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from github_issue_worker.adapters.agent_version import version_of
from github_issue_worker.adapters.installations import (
    EXECUTABLES,
    Unavailable,
    portable_tool,
    resolve,
)
from github_issue_worker.adapters.installations import portable_roots as find_portable_roots
from github_issue_worker.configuration import WorkerSettings

PRIORITY = {"claude": 1, "codex": 2, "antigravity": 3, "opencode": 5}
"""The priority a found agent is proposed with: lower is preferred."""


def probe_version(proposal: dict[str, Any], home: Path) -> str:
    """The version the proposed worker's installation answers with; `Unavailable` says why not."""
    return version_of(resolve(WorkerSettings.model_validate(proposal), home))


PORTABLE_TOOLS = ("claude", "codex", "antigravity")
"""The tools of a ClaudeCodePortable profile a worker can run."""


def portable_profiles(root: Path) -> list[dict[str, Any]]:
    """Every profile of a ClaudeCodePortable installation as a proposed worker; one that cannot
    be one carries the ``problem`` why: an unsupported tool, or its CLI not installed."""
    proposals: list[dict[str, Any]] = []
    for profile in sorted((root / "profiles").glob("*"), key=lambda p: p.name.casefold()):
        if not profile.is_dir():
            continue
        marker = profile / ".tool"
        kind = marker.read_text(encoding="utf-8").strip() if marker.exists() else "claude"
        proposal: dict[str, Any] = {
            "name": kind,
            "agent": kind,
            "account": f"{kind}-{profile.name}",
            "priority": PRIORITY.get(kind, 100),
            "installation": {
                "type": "claudecodeportable",
                "root": str(root),
                "profile": profile.name,
            },
        }
        if kind not in PORTABLE_TOOLS:
            proposal["problem"] = f"{kind} is not a supported agent"
        elif not portable_tool(profile, kind).exists():
            proposal["problem"] = f"{kind} is not installed in profile {profile.name}"
        proposals.append(proposal)
    return proposals


def preview_portable(
    root: str,
    home: Path,
    probe: Callable[[dict[str, Any], Path], str] = probe_version,
) -> list[dict[str, Any]]:
    """What a ClaudeCodePortable folder offers, each profile with its version or problem; empty
    when the folder is no such installation (F14.4)."""
    folder = home / root[2:] if root == "~" or root.startswith(("~/", "~\\")) else Path(root)
    proposals = portable_profiles(folder)
    _probe_all(proposals, home, probe)
    return proposals


def _probe_all(
    proposals: list[dict[str, Any]], home: Path, probe: Callable[[dict[str, Any], Path], str]
) -> None:
    for proposed in proposals:
        if "problem" in proposed:
            continue
        try:
            proposed["version"] = probe(proposal(proposed), home)
        except Unavailable as problem:
            proposed["problem"] = str(problem)


def detect_workers(
    home: Path,
    portable_roots: Sequence[str] = (),
    probe: Callable[[dict[str, Any], Path], str] = probe_version,
) -> list[dict[str, Any]]:
    """One proposed worker per agent installation found: ClaudeCodePortable profiles under the
    default, the configured and the current session's root, ClaudeLocal, and the agents on PATH.

    Each carries the ``version`` its installation answered with, or the ``problem`` why it did
    not; only a proven one may be added (F15.9).
    """
    found: list[dict[str, Any]] = []
    for portable in find_portable_roots(home, portable_roots, os.environ):
        found += [p for p in portable_profiles(portable) if "problem" not in p]

    local = home / "ClaudeLocal"
    if (local / "claude.sh").exists() or (local / "Claude.bat").exists():
        found.append(
            {
                "name": "local",
                "agent": "claude",
                "account": "claudelocal",
                "priority": 9,
                "installation": {"type": "claudelocal", "root": str(local)},
            }
        )

    for kind, executable in EXECUTABLES.items():
        if shutil.which(executable) or shutil.which(kind):
            found.append({"name": kind, "agent": kind, "account": kind, "priority": PRIORITY[kind]})
            found[-1]["installation"] = {"type": "path"}

    names: dict[str, int] = {}
    for worker in found:
        names[worker["name"]] = names.get(worker["name"], 0) + 1
        if names[worker["name"]] > 1:
            worker["name"] = f"{worker['name']}-{names[worker['name']]}"
    _probe_all(found, home, probe)
    return found


FOUND_ONLY = ("version", "problem")
"""What detection reports about a found installation that is not part of a worker's settings."""


def proposal(found: dict[str, Any]) -> dict[str, Any]:
    """The worker settings of a found installation, without what detection reports about it."""
    return {key: value for key, value in found.items() if key not in FOUND_ONLY}
