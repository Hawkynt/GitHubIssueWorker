from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from github_issue_worker.configuration import (
    Configuration,
    ConfigurationError,
    default_path,
    load,
    parse,
    save,
)

README = Path(__file__).resolve().parent.parent / "README.md"


def readme_example() -> str:
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## ⚙️ Configuration") :]
    match = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
    assert match, "the Configuration section carries a JSON example"
    return match.group(1)


def error_paths(text: str) -> list[str]:
    with pytest.raises(ConfigurationError) as caught:
        parse(text)
    return [problem.path for problem in caught.value.problems]


def test_an_empty_file_is_a_valid_configuration_of_defaults() -> None:
    config = parse("{}")
    assert config.poll_interval == timedelta(minutes=1)
    assert config.fleet.claim_validity == timedelta(days=7)
    assert config.fleet.claim_grace == timedelta(days=1)
    assert config.fleet.stop_claiming_at == 0.9
    assert config.fleet.local_hand_off is True
    assert config.fleet.hand_off_when_paused_longer_than is None
    assert config.defaults.max_active_issues_per_repository == 4
    assert config.ui.port == 8765
    assert config.ui.bind == "127.0.0.1"
    assert config.workers == []


def test_the_readme_example_is_valid() -> None:
    config = parse(readme_example())
    assert [worker.name for worker in config.workers] == ["claude", "codex", "antigravity", "local"]
    assert config.workers[3].installation.type == "claudelocal"
    assert config.accounts["local-gpu"].max_concurrent_runs == 1
    assert config.repositories[1].mode == "labeled"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"fleet": {"claimValidty": "7.00:00:00"}}', "fleet.claimValidty"),
        ('{"pollInterval": "soon"}', "pollInterval"),
        ('{"pollInterval": "00:61:00"}', "pollInterval"),
        ('{"fleet": {"stopClaimingAt": 1.5}}', "fleet.stopClaimingAt"),
        ('{"repositories": [{"repository": "not a repo"}]}', "repositories[0].repository"),
        (
            '{"workers": [{"name": "a", "agent": "claude", "account": "x"}]}',
            "workers[0].account",
        ),
        (
            '{"accounts": {"x": {}}, "workers": [{"name": "a", "agent": "robot", "account": "x"}]}',
            "workers[0].agent",
        ),
        (
            '{"accounts": {"x": {}}, "workers": [{"name": "a", "agent": "claude", "account": "x",'
            ' "installation": {"type": "magic"}}]}',
            "workers[0].installation",
        ),
    ],
)
def test_an_invalid_key_is_reported_by_its_json_path(text: str, expected: str) -> None:
    assert expected in error_paths(text)


def test_worker_names_are_unique() -> None:
    text = json.dumps(
        {
            "accounts": {"x": {}},
            "workers": [
                {"name": "a", "agent": "claude", "account": "x"},
                {"name": "a", "agent": "codex", "account": "x"},
            ],
        }
    )
    assert "workers[1].name" in error_paths(text)


def test_a_command_worker_needs_a_command() -> None:
    text = '{"accounts": {"x": {}}, "workers": [{"name": "a", "agent": "command", "account": "x"}]}'
    assert "workers[0].command" in error_paths(text)


def test_claudelocal_serves_only_claude_workers() -> None:
    text = json.dumps(
        {
            "accounts": {"x": {}},
            "workers": [
                {
                    "name": "a",
                    "agent": "codex",
                    "account": "x",
                    "installation": {"type": "claudelocal"},
                }
            ],
        }
    )
    assert "workers[0].installation.type" in error_paths(text)


def test_a_non_loopback_ui_needs_a_token() -> None:
    assert "ui.token" in error_paths('{"ui": {"bind": "0.0.0.0"}}')
    assert parse('{"ui": {"bind": "0.0.0.0", "token": "s3cret"}}').ui.token == "s3cret"


def test_all_problems_are_reported_at_once() -> None:
    paths = error_paths('{"pollInterval": "x", "fleet": {"maxTakeovers": -1}}')
    assert {"pollInterval", "fleet.maxTakeovers"} <= set(paths)


def test_durations_accept_days_and_round_trip(tmp_path: Path) -> None:
    config = parse('{"fleet": {"claimValidity": "7.00:00:00", "checkpointInterval": "00:05:30"}}')
    assert config.fleet.claim_validity == timedelta(days=7)
    assert config.fleet.checkpoint_interval == timedelta(minutes=5, seconds=30)

    path = tmp_path / "config.json"
    save(config, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"fleet": {"claimValidity": "7.00:00:00", "checkpointInterval": "00:05:30"}}


def test_save_writes_only_what_was_set_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.json"
    config = parse('{"trustedUsers": ["octocat"]}')
    save(config, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"trustedUsers": ["octocat"]}
    assert [p.name for p in path.parent.iterdir()] == ["config.json"]
    assert load(path) == config


def test_save_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"trustedUsers": ["old"]}', encoding="utf-8")
    save(parse('{"trustedUsers": ["new"]}'), path)
    assert load(path).trusted_users == ["new"]


def test_a_missing_file_loads_as_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "absent.json") == Configuration()


def test_invalid_json_is_a_configuration_error() -> None:
    assert error_paths("{not json") == ["$"]


def test_explicit_values_are_distinguishable_from_defaults() -> None:
    config = parse('{"fleet": {"maxTakeovers": 3}}')
    assert config.fleet.is_set("max_takeovers")
    assert not config.fleet.is_set("claim_validity")


def test_default_path_follows_the_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_path(platform="linux") == tmp_path / "xdg" / "GitHubIssueWorker" / "config.json"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    expected = tmp_path / "home" / ".config" / "GitHubIssueWorker" / "config.json"
    assert default_path(platform="linux") == expected

    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    expected = tmp_path / "roaming" / "GitHubIssueWorker" / "config.json"
    assert default_path(platform="win32") == expected
