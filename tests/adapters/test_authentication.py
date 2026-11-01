from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from github_issue_worker.adapters.agents.claude import ClaudeAdapter
from github_issue_worker.ports.agents import AgentRun


def test_claude_login_status_is_checked_without_starting_a_model(tmp_path: Path) -> None:
    adapter = ClaudeAdapter("profile-claude", env={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    with patch("github_issue_worker.adapters.agents.claude.subprocess.run") as call:
        call.return_value = subprocess.CompletedProcess(
            [], 1, '{"loggedIn":false,"authMethod":"none"}', ""
        )
        status = adapter.authentication()
    assert status.authenticated is False
    assert "login" in status.detail.lower() and str(tmp_path) in status.detail
    assert call.call_args.args[0] == ["profile-claude", "auth", "status"]
    assert call.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)


def test_a_logged_in_cli_clears_the_authentication_block() -> None:
    with patch("github_issue_worker.adapters.agents.claude.subprocess.run") as call:
        call.return_value = subprocess.CompletedProcess([], 0, '{"loggedIn":true}', "")
        assert ClaudeAdapter("claude").authentication().authenticated is True


def test_unknown_login_status_is_not_guessed_to_be_logged_in() -> None:
    with patch("github_issue_worker.adapters.agents.claude.subprocess.run") as call:
        call.return_value = subprocess.CompletedProcess([], 1, "unknown command auth", "")
        assert ClaudeAdapter("claude").authentication().authenticated is None


def test_expired_login_is_distinct_from_quota_and_from_issue_failure(tmp_path: Path) -> None:
    output = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "OAuth token has expired. Please run /login.",
            "api_error": "authentication_error",
        }
    )
    report = ClaudeAdapter("claude").report(AgentRun(tmp_path, "work"), 1, output, "")
    assert report.authentication is not None
    assert report.limit is None and not report.did_work
