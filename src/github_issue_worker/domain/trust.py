"""Whose words may reach an agent (F2.4)."""

from __future__ import annotations

from collections.abc import Iterable

from github_issue_worker.domain.issue import Actor

WRITE_ROLES = frozenset({"admin", "maintain", "write"})
"""Repository roles that can push. ``triage`` and ``read`` cannot, whatever their association."""


def is_trusted(actor: Actor, role: str | None, trusted_users: Iterable[str]) -> bool:
    """Trusted: not a bot, and either holding a write role or on the allowlist."""
    if actor.is_bot:
        return False
    if actor.login.lower() in {login.lower() for login in trusted_users}:
        return True
    return role in WRITE_ROLES
