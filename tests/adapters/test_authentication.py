from __future__ import annotations

import pytest

from github_issue_worker.adapters.authentication import AuthenticationError, resolve_token


def gh_returning(code: int, output: str) -> object:
    calls: list[dict[str, str]] = []

    def run(env: dict[str, str]) -> tuple[int, str]:
        calls.append(env)
        return code, output

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_gh_token_wins() -> None:
    gh = gh_returning(0, "from-gh\n")
    env = {"GH_TOKEN": "a", "GITHUB_TOKEN": "b"}
    assert resolve_token(env, gh) == "a"  # type: ignore[arg-type]
    assert gh.calls == []  # type: ignore[attr-defined]


def test_github_token_comes_second() -> None:
    assert resolve_token({"GITHUB_TOKEN": "b"}, gh_returning(0, "x")) == "b"  # type: ignore[arg-type]


def test_gh_auth_token_comes_last_and_sees_gh_config_dir() -> None:
    gh = gh_returning(0, "gho_abc\n")
    env = {"GH_CONFIG_DIR": "/home/someone/.config/gh"}
    assert resolve_token(env, gh) == "gho_abc"  # type: ignore[arg-type]
    assert gh.calls[0]["GH_CONFIG_DIR"] == "/home/someone/.config/gh"  # type: ignore[attr-defined]


def test_blank_variables_are_skipped() -> None:
    assert resolve_token({"GH_TOKEN": "  "}, gh_returning(0, "gho_x")) == "gho_x"  # type: ignore[arg-type]


@pytest.mark.parametrize(("code", "output"), [(1, ""), (0, "\n")])
def test_no_token_anywhere_names_the_fix(code: int, output: str) -> None:
    with pytest.raises(AuthenticationError, match="gh auth login"):
        resolve_token({}, gh_returning(code, output))  # type: ignore[arg-type]


def test_a_missing_gh_names_the_fix() -> None:
    def missing(env: dict[str, str]) -> tuple[int, str]:
        raise FileNotFoundError("gh")

    with pytest.raises(AuthenticationError, match="GH_TOKEN"):
        resolve_token({}, missing)
