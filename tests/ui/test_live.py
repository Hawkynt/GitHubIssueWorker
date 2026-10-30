from __future__ import annotations

import pytest

from github_issue_worker.ui.live import issue_state


@pytest.mark.parametrize(
    ("labels", "claim_state", "local", "expected"),
    [
        (set(), "working", True, "worked here"),
        (set(), "working", False, "worked elsewhere"),
        (set(), "paused", False, "paused until the usage limit resets"),
        ({"worker:queued"}, None, False, "waiting for a worker"),
        ({"worker:needs-input"}, None, False, "waiting for your answer"),
        ({"worker:awaiting-review"}, None, False, "waiting for review or CI"),
        ({"worker:failed"}, None, False, "failed; its last comment says why"),
        ({"worker:unserved"}, None, False, "no worker can serve it"),
        ({"worker:skip", "bug"}, None, False, "skipped (worker:skip)"),
        ({"bug"}, None, False, "not picked up"),
    ],
)
def test_an_issue_says_what_is_happening(
    labels: set[str], claim_state: str | None, local: bool, expected: str
) -> None:
    assert issue_state(frozenset(labels), claim_state, local) == expected
