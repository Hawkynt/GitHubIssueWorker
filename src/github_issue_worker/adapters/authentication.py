"""Where the manager's GitHub token comes from (F0.5): ``GH_TOKEN``, ``GITHUB_TOKEN``, ``gh``."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping

GhRunner = Callable[[dict[str, str]], tuple[int, str]]
"""Runs ``gh auth token`` with the given environment and returns its exit code and output."""


class AuthenticationError(Exception):
    """No usable token. The message names the command that fixes it."""


def run_gh_auth_token(env: dict[str, str]) -> tuple[int, str]:
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, env=env, check=False
    )
    return result.returncode, result.stdout


def resolve_token(environ: Mapping[str, str], gh: GhRunner = run_gh_auth_token) -> str:
    """The first non-blank of ``GH_TOKEN``, ``GITHUB_TOKEN`` and ``gh auth token``.

    ``gh`` runs with ``environ`` unchanged, so ``GH_CONFIG_DIR`` points it at a configuration kept
    under another home directory.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = environ.get(name, "").strip()
        if value:
            return value

    try:
        code, output = gh(dict(environ))
    except FileNotFoundError:
        raise AuthenticationError(
            "no GitHub token: set GH_TOKEN, or install gh and run `gh auth login`"
        ) from None

    token = output.strip()
    if code != 0 or not token:
        raise AuthenticationError(
            "no GitHub token: run `gh auth login` (set GH_CONFIG_DIR if gh is configured under "
            "another home directory), or set GH_TOKEN"
        )
    return token
