from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from github_issue_worker.adapters.installations import (
    Unavailable,
    portable_roots,
    portable_tool,
    resolve,
)
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
    agy = executable(tmp_path / "bin" / ("agy.cmd" if sys.platform == "win32" else "agy"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    resolved = resolve(worker("antigravity"))  # type: ignore[arg-type]
    assert resolved.executable.name.lower() == agy.name.lower()
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


def command_worker(program: str, installation: dict[str, object] | None = None) -> object:
    import json

    body: dict[str, object] = {
        "name": "w",
        "agent": "command",
        "account": "a",
        "command": [program, "{prompt}"],
    }
    if installation:
        body["installation"] = installation
    return parse(json.dumps({"accounts": {"a": {}}, "workers": [body]})).workers[0]


def test_path_finds_a_command_worker_s_program_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = executable(tmp_path / "bin" / ("mycli.cmd" if sys.platform == "win32" else "mycli"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    resolved = resolve(command_worker("mycli"))  # type: ignore[arg-type]
    assert resolved.executable.name.lower() == tool.name.lower()


def test_path_takes_a_command_worker_s_program_given_as_a_path(tmp_path: Path) -> None:
    tool = executable(tmp_path / "tools" / "mycli")
    if sys.platform == "win32":
        tool = executable(tmp_path / "tools" / "mycli.exe")
    assert resolve(command_worker(str(tool))).executable == tool  # type: ignore[arg-type]


def test_path_reports_a_command_worker_s_missing_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(Unavailable, match="mycli"):
        resolve(command_worker("mycli"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("agent", "variable", "value"),
    [
        ("claude", "CLAUDE_CONFIG_DIR", "claude-config"),
        ("codex", "CODEX_HOME", "codex-config"),
        ("antigravity", "HOME", ""),
    ],
)
def test_claudecodeportable_resolves_inside_the_profile(
    tmp_path: Path, agent: str, variable: str, value: str
) -> None:
    root = tmp_path / "ClaudeCodePortable"
    profile = root / "profiles" / "P"
    tool = executable(portable_tool(profile, agent))
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
    name = "Claude.bat" if sys.platform == "win32" else "claude.sh"
    launcher = executable(tmp_path / "ClaudeLocal" / name)
    resolved = resolve(
        worker("claude", {"type": "claudelocal", "root": str(tmp_path / "ClaudeLocal")})  # type: ignore[arg-type]
    )
    assert resolved.executable == launcher and resolved.local


@pytest.mark.parametrize(
    ("agent", "windows", "relative"),
    [
        # npm puts its shims straight into the prefix on Windows and into prefix/bin elsewhere.
        ("claude", True, "npm-global/claude.cmd"),
        ("codex", True, "npm-global/codex.cmd"),
        ("claude", False, "npm-global/bin/claude"),
        ("codex", False, "npm-global/bin/codex"),
        # The launcher redirects USERPROFILE/HOME, and agy installs relative to it.
        ("antigravity", True, "AppData/Local/agy/bin/agy.exe"),
        ("antigravity", False, ".local/bin/agy"),
    ],
)
def test_given_a_platform_when_locating_a_portable_cli_then_its_layout_is_used(
    tmp_path: Path, agent: str, windows: bool, relative: str
) -> None:
    assert portable_tool(tmp_path, agent, windows) == tmp_path / relative


def portable_root(path: Path, *profiles: str) -> Path:
    for profile in profiles:
        (path / "profiles" / profile).mkdir(parents=True, exist_ok=True)
    return path


def test_given_no_configured_root_when_searching_then_only_the_home_default_is_used(
    tmp_path: Path,
) -> None:
    portable_root(tmp_path / "ClaudeCodePortable", "default")
    assert portable_roots(tmp_path, [], {}) == [tmp_path / "ClaudeCodePortable"]


def test_given_configured_roots_when_searching_then_existing_ones_follow_the_default(
    tmp_path: Path,
) -> None:
    portable_root(tmp_path / "ClaudeCodePortable", "default")
    elsewhere = portable_root(tmp_path / "Agents" / "ClaudePortable", "work")
    missing = tmp_path / "nowhere"
    found = portable_roots(tmp_path, [str(elsewhere), str(missing)], {})
    assert found == [tmp_path / "ClaudeCodePortable", elsewhere]


def test_given_a_tilde_root_when_searching_then_it_means_the_account_home(tmp_path: Path) -> None:
    root = portable_root(tmp_path / "Tools" / "Portable", "p")
    assert portable_roots(tmp_path, ["~/Tools/Portable"], {}) == [root]


def test_given_the_same_root_twice_when_searching_then_it_is_listed_once(tmp_path: Path) -> None:
    root = portable_root(tmp_path / "ClaudeCodePortable", "p")
    environ = {"CLAUDE_CONFIG_DIR": str(root / "profiles" / "p" / "claude-config")}
    assert portable_roots(tmp_path, [str(root), str(root) + os.sep], environ) == [root]


@pytest.mark.parametrize(
    ("variable", "leaf"), [("CLAUDE_CONFIG_DIR", "claude-config"), ("CODEX_HOME", "codex-config")]
)
def test_given_a_portable_session_when_searching_then_its_root_is_inferred(
    tmp_path: Path, variable: str, leaf: str
) -> None:
    root = portable_root(tmp_path / "Agents" / "ClaudePortable", "me")
    environ = {variable: str(root / "profiles" / "me" / leaf)}
    assert portable_roots(tmp_path, [], environ) == [root]


@pytest.mark.parametrize("value", ["", "relative", "/a", "/not/a/profiles/dir/claude-config"])
def test_given_an_unrelated_config_dir_when_searching_then_nothing_is_inferred(
    tmp_path: Path, value: str
) -> None:
    assert portable_roots(tmp_path, [], {"CLAUDE_CONFIG_DIR": value}) == []
