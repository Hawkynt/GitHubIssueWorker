from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from github_issue_worker.adapters.git_claims import GitClaimStore


def test_given_a_deferred_clone_when_reading_claims_then_open_once_on_first_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone = Mock(return_value=tmp_path)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, b"", b""))
    monkeypatch.setattr(subprocess, "run", run)
    store = GitClaimStore(clone, home=tmp_path)
    clone.assert_not_called()
    assert store.issues() == []
    assert store.issues() == []
    clone.assert_called_once_with()
    assert all(call.kwargs["cwd"] == tmp_path for call in run.call_args_list)
