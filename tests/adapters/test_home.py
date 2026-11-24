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


def _git_config(env: dict[str, str]) -> list[tuple[str, str]]:
    count = int(env["GIT_CONFIG_COUNT"])
    return [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(count)]


def test_networked_git_authenticates_through_gh_alone() -> None:
    env = tool_environment(Path("/home/me"), base={}, platform="linux")
    # The empty value drops every helper configured before (e.g. a system credential manager).
    assert _git_config(env) == [
        ("credential.https://github.com.helper", ""),
        ("credential.https://github.com.helper", "!gh auth git-credential"),
    ]


def test_git_config_entries_already_in_the_environment_are_kept() -> None:
    base = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.x", "GIT_CONFIG_VALUE_0": "1"}
    env = tool_environment(Path("/home/me"), base=base, platform="linux")
    assert _git_config(env) == [
        ("core.x", "1"),
        ("credential.https://github.com.helper", ""),
        ("credential.https://github.com.helper", "!gh auth git-credential"),
    ]


@pytest.mark.parametrize("count", ["", "x", "-1"])
def test_an_unusable_git_config_count_is_started_over(count: str) -> None:
    env = tool_environment(Path("/home/me"), base={"GIT_CONFIG_COUNT": count}, platform="linux")
    assert len(_git_config(env)) == 2
