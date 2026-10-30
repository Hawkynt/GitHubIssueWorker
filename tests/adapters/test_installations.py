from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from github_issue_worker.adapters.installations import Unavailable, resolve
from github_issue_worker.configuration import parse


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def worker(agent: str, installation: dict[str, str] | None = None) -> object:
    body = {"name": "w", "agent": agent, "account": "a"}
    if installation:
        body["installation"] = installation  # type: ignore[assignment]
    import json

    return parse(json.dumps({"accounts": {"a": {}}, "workers": [body]})).workers[0]


def test_path_finds_the_agent_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = executable(tmp_path / "bin" / "agy")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    resolved = resolve(worker("antigravity"))  # type: ignore[arg-type]
    assert resolved.executable == agy
    assert resolved.env == {}


def test_path_reports_a_missing_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(Unavailable, match="codex"):
        resolve(worker("codex"))  # type: ignore[arg-type]


def test_executable_is_taken_as_given_with_its_environment(tmp_path: Path) -> None:
    tool = executable(tmp_path / "my-codex")
    resolved = resolve(
        worker("codex", {"type": "executable", "path": str(tool), "env": {"X": "1"}})  # type: ignore[arg-type,dict-item]
    )
    assert resolved.executable == tool and resolved.env == {"X": "1"}


@pytest.mark.parametrize(
    ("agent", "relative", "variable", "value"),
    [
        ("claude", "npm-global/bin/claude", "CLAUDE_CONFIG_DIR", "claude-config"),
        ("codex", "npm-global/bin/codex", "CODEX_HOME", "codex-config"),
        ("antigravity", ".local/bin/agy", "HOME", ""),
    ],
)
def test_claudecodeportable_resolves_inside_the_profile(
    tmp_path: Path, agent: str, relative: str, variable: str, value: str
) -> None:
    root = tmp_path / "ClaudeCodePortable"
    profile = root / "profiles" / "P"
    tool = executable(profile / relative)
    node = executable(root / "app" / "node" / "node-v22.23.3-linux-x64" / "bin" / "node")

    resolved = resolve(
        worker(agent, {"type": "claudecodeportable", "root": str(root), "profile": "P"})  # type: ignore[arg-type]
    )
    assert resolved.executable == tool
    assert resolved.env[variable] == (str(profile / value) if value else str(profile))
    # The npm-installed CLIs are node scripts: the portable node must be on PATH.
    assert resolved.env["PATH"].split(os.pathsep)[0] == str(node.parent)


def test_claudecodeportable_names_a_missing_profile(tmp_path: Path) -> None:
    with pytest.raises(Unavailable, match="profile"):
        resolve(
            worker(
                "codex", {"type": "claudecodeportable", "root": str(tmp_path), "profile": "nope"}
            )  # type: ignore[arg-type]
        )


def test_claudelocal_resolves_its_launcher(tmp_path: Path) -> None:
    launcher = executable(tmp_path / "ClaudeLocal" / "claude.sh")
    resolved = resolve(
        worker("claude", {"type": "claudelocal", "root": str(tmp_path / "ClaudeLocal")})  # type: ignore[arg-type]
    )
    assert resolved.executable == launcher and resolved.local
