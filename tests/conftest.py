from __future__ import annotations

import pytest

from github_issue_worker.adapters.installations import PORTABLE_SESSION_VARIABLES


@pytest.fixture(autouse=True)
def outside_a_portable_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker detection infers a ClaudeCodePortable root from these variables; a test run started
    inside such a session must not find the host's own profiles."""
    for variable in PORTABLE_SESSION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
