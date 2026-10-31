from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from github_issue_worker.configuration import (
    AccountSettings,
    Configuration,
    ConfigurationError,
    default_path,
    load,
    parse,
    save,
    validate,
)
from github_issue_worker.domain.commit_message import DEFAULT_FORBIDDEN_WORDS

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
    assert config.manager.home is None


def test_the_readme_example_is_valid() -> None:
    config = parse(readme_example())
    assert [worker.name for worker in config.workers] == ["claude", "codex", "antigravity", "local"]
    assert config.workers[3].installation.type == "claudelocal"
    assert config.accounts["local-gpu"].max_concurrent_runs == 1
    assert config.repositories[1].mode == "labeled"


def test_every_maker_has_a_hue_segment_by_default() -> None:
    colors = parse("{}").model_colors
    assert (colors.anthropic.hue, colors.anthropic.width) == (70, 90)
    assert (colors.openai.hue, colors.openai.width) == (250, 55)
    assert (colors.google.hue, colors.google.width) == (155, 55)
    assert (colors.other.hue, colors.other.width) == (320, 120)


def test_a_hue_off_the_wheel_is_refused() -> None:
    paths = error_paths('{"modelColors": {"anthropic": {"hue": 400}}}')
    assert paths == ["modelColors.anthropic.hue"]


def test_a_segment_wider_than_the_wheel_is_refused() -> None:
    assert error_paths('{"modelColors": {"other": {"width": 400}}}') == ["modelColors.other.width"]


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


def test_leaving_forbidden_words_out_uses_the_default_list() -> None:
    config = parse('{"repositories": [{"repository": "Hawkynt/ShiftDiff"}]}')
    assert tuple(config.defaults.forbidden_words) == DEFAULT_FORBIDDEN_WORDS
    assert not config.defaults.is_set("forbidden_words")
    assert config.repositories[0].forbidden_words is None


def test_forbidden_words_can_be_set_in_defaults_and_overridden_per_repository(
    tmp_path: Path,
) -> None:
    raw = {
        "defaults": {"forbiddenWords": ["claude", "codex"]},
        "repositories": [
            {"repository": "Hawkynt/ShiftDiff"},
            {"repository": "Hawkynt/GitHubIssueWorker", "forbiddenWords": []},
        ],
    }
    config = parse(json.dumps(raw))
    assert config.defaults.forbidden_words == ["claude", "codex"]
    assert config.defaults.is_set("forbidden_words")
    assert config.repositories[0].forbidden_words is None
    assert config.repositories[1].forbidden_words == []

    path = tmp_path / "config.json"
    save(config, path)
    assert json.loads(path.read_text(encoding="utf-8")) == raw
    assert load(path) == config


def test_the_readme_configuration_table_documents_forbidden_words() -> None:
    text = README.read_text(encoding="utf-8")
    section = text[text.index("## ⚙️ Configuration") : text.index("## 🛡️ Security")]
    assert "| `forbiddenWords` |" in section


def test_concurrent_runs_default_to_one_and_stop_at_twenty() -> None:
    assert AccountSettings().max_concurrent_runs == 1
    for wrong in (0, 21, None):
        with pytest.raises(ValidationError):
            AccountSettings(max_concurrent_runs=wrong)  # type: ignore[arg-type]


def test_a_worker_name_cannot_look_like_an_instance() -> None:
    data = {
        "accounts": {"a": {}},
        "workers": [{"name": "claude.2", "agent": "claude", "account": "a"}],
    }
    with pytest.raises(ConfigurationError) as raised:
        validate(data)
    assert "workers[0].name" in str(raised.value)
