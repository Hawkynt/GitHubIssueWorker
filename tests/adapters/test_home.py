from __future__ import annotations

from pathlib import Path

import pytest

from github_issue_worker.adapters import home
from github_issue_worker.adapters.home import account_home, tool_environment


def test_the_account_home_comes_from_the_user_database_not_from_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/me/ClaudeCodePortable/profiles/default")
    monkeypatch.setattr(home, "_passwd_home", lambda: "/home/me")
    assert account_home(platform="linux") == Path("/home/me")


def test_a_configured_home_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(home, "_passwd_home", lambda: "/home/me")
    assert account_home(configured="/srv/worker", platform="linux") == Path("/srv/worker")


def test_tools_run_with_the_account_home() -> None:
    base = {"HOME": "/home/me/ClaudeCodePortable/profiles/default", "PATH": "/usr/bin"}
    env = tool_environment(Path("/home/me"), base=base, platform="linux")
    assert env["HOME"] == str(Path("/home/me"))
    assert env["PATH"] == "/usr/bin"


def test_a_config_home_inside_the_relocated_home_is_dropped() -> None:
    relocated = "/home/me/ClaudeCodePortable/profiles/default"
    base = {"HOME": relocated, "XDG_CONFIG_HOME": f"{relocated}/.config"}
    assert "XDG_CONFIG_HOME" not in tool_environment(Path("/home/me"), base=base, platform="linux")


def test_a_deliberate_config_home_elsewhere_is_kept() -> None:
    base = {"HOME": "/home/me/portable", "XDG_CONFIG_HOME": "/etc/xdg-me"}
    env = tool_environment(Path("/home/me"), base=base, platform="linux")
    assert env["XDG_CONFIG_HOME"] == "/etc/xdg-me"


def test_an_explicit_gh_config_dir_is_respected() -> None:
    base = {"HOME": "/x", "GH_CONFIG_DIR": "/home/me/.config/gh-work"}
    env = tool_environment(Path("/home/me"), base=base, platform="linux")
    assert env["GH_CONFIG_DIR"] == "/home/me/.config/gh-work"


def test_on_windows_the_profile_variables_follow_the_account_home() -> None:
    base = {"USERPROFILE": r"D:\Portable\profiles\default", "HOME": r"D:\Portable\profiles\default"}
    env = tool_environment(Path(r"C:\Users\me"), base=base, platform="win32")
    assert env["USERPROFILE"] == str(Path(r"C:\Users\me"))
    assert env["HOME"] == str(Path(r"C:\Users\me"))
