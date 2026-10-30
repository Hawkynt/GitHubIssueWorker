from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from github_issue_worker.cli import detect_workers, main, normalize_issue, normalize_repository
from github_issue_worker.configuration import load
from github_issue_worker.ports.github import GhUnavailable, Response
from github_issue_worker.ports.repository import RepositoryInfo


@pytest.mark.parametrize(
    "text",
    [
        "Hawkynt/ShiftDiff",
        "https://github.com/Hawkynt/ShiftDiff",
        "https://github.com/Hawkynt/ShiftDiff.git",
        "https://github.com/Hawkynt/ShiftDiff/issues/12",
        "git@github.com:Hawkynt/ShiftDiff.git",
    ],
)
def test_repositories_are_normalised(text: str) -> None:
    assert normalize_repository(text) == "Hawkynt/ShiftDiff"


def test_issue_references_are_parsed() -> None:
    assert normalize_issue("Hawkynt/ShiftDiff#12") == ("Hawkynt/ShiftDiff", 12)
    assert normalize_issue("https://github.com/Hawkynt/ShiftDiff/issues/12") == (
        "Hawkynt/ShiftDiff",
        12,
    )
    with pytest.raises(ValueError):
        normalize_issue("Hawkynt/ShiftDiff")


class Host:
    def __init__(self, can_push: bool) -> None:
        self.can_push = can_push
        self.labelled = False

    def info(self) -> RepositoryInfo:
        return RepositoryInfo("main", self.can_push, ("squash",))

    def ensure_worker_labels(self) -> None:
        self.labelled = True


def test_add_verifies_push_access_and_creates_the_labels(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    host = Host(can_push=True)
    assert (
        main(
            ["--config", str(config), "add", "https://github.com/Hawkynt/ShiftDiff"],
            host_factory=lambda repo: host,
        )
        == 0
    )
    assert host.labelled
    assert [r.repository for r in load(config).repositories] == ["Hawkynt/ShiftDiff"]

    assert (
        main(["--config", str(config), "add", "Hawkynt/ShiftDiff"], host_factory=lambda r: host)
        == 0
    )
    assert len(load(config).repositories) == 1  # adding twice is a no-op


def test_add_refuses_a_repository_without_push_access(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    assert main(["--config", str(config), "add", "o/r"], host_factory=lambda repo: Host(False)) == 1
    assert "push" in capsys.readouterr().err
    assert not config.exists()


def test_remove_and_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"repositories": [{"repository": "o/a"}, {"repository": "o/b"}]}))
    assert main(["--config", str(config), "remove", "o/a"]) == 0
    capsys.readouterr()
    assert main(["--config", str(config), "list"]) == 0
    assert capsys.readouterr().out.split() == ["o/b"]


class Api:
    """The GitHub adapter as the CLI sees it: every request recorded, answered, or refused the way
    an unlogged-in `gh` refuses — no HTTP response at all."""

    def __init__(self, logged_in: bool = True) -> None:
        self.logged_in = logged_in
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_: object) -> Response:
        self.requests.append((method, path))
        if not self.logged_in:
            raise GhUnavailable("To get started with GitHub CLI, please run:  gh auth login")
        return Response(status=200, body={"login": "hawkynt"})


class Watch:
    def __init__(self, host: Host) -> None:
        self.host = host


class Manager:
    def __init__(self, hosts: list[Host] | None = None) -> None:
        self.repositories: list[Watch] = [Watch(host) for host in hosts or []]
        self.workers: list[object] = []
        self.ticks = 0

    def snapshot(self) -> list[dict[str, object]]:
        return []

    def tick(self) -> None:
        self.ticks += 1


class Runtime:
    def __init__(self, api: Api, manager: Manager) -> None:
        self.api = api
        self.manager_id = "manager@host"
        self._manager = manager

    def manager(self) -> Manager:
        return self._manager


def run_watch(config: Path, api: Api, manager: Manager) -> int:
    return main(
        ["--config", str(config), "watch", "--once"],
        home=config.parent,
        runtime_factory=lambda *a: Runtime(api, manager),
    )


def watched_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"repositories": [{"repository": "o/a"}]}))
    return config


def test_watch_stops_when_gh_cannot_reach_github(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(can_push=True)
    api, manager = Api(logged_in=False), Manager([host])
    assert run_watch(watched_config(tmp_path), api, manager) == 2
    assert "gh auth login" in capsys.readouterr().err
    assert api.requests == [("GET", "user")]
    assert manager.ticks == 0
    assert not host.labelled


def test_watch_polls_when_gh_is_logged_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(can_push=True)
    api, manager = Api(), Manager([host])
    assert run_watch(watched_config(tmp_path), api, manager) == 0
    assert api.requests == [("GET", "user")]
    assert host.labelled
    assert manager.ticks == 1
    assert "watching 1 repositories" in capsys.readouterr().out


def tool(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_detection_finds_portable_profiles_claudelocal_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portable = tmp_path / "ClaudeCodePortable" / "profiles"
    tool(portable / "default" / "npm-global" / "bin" / "claude")
    tool(portable / "ChatGPTCodex" / "npm-global" / "bin" / "codex")
    (portable / "ChatGPTCodex" / ".tool").write_text("codex", encoding="utf-8")
    tool(portable / "GoogleGemini" / ".local" / "bin" / "agy")
    (portable / "GoogleGemini" / ".tool").write_text("antigravity", encoding="utf-8")
    tool(tmp_path / "ClaudeLocal" / "claude.sh")
    tool(tmp_path / "bin" / "opencode")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    found = {(w["name"], w["agent"], w["installation"]["type"]) for w in detect_workers(tmp_path)}
    assert found == {
        ("claude", "claude", "claudecodeportable"),
        ("codex", "codex", "claudecodeportable"),
        ("antigravity", "antigravity", "claudecodeportable"),
        ("local", "claude", "claudelocal"),
        ("opencode", "opencode", "path"),
    }


def test_workers_detect_save_writes_workers_and_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    tool(tmp_path / "ClaudeLocal" / "claude.sh")
    config = tmp_path / "config.json"
    assert main(["--config", str(config), "workers", "--detect", "--save"], home=tmp_path) == 0
    saved = load(config)
    assert [w.name for w in saved.workers] == ["local"]
    assert saved.accounts["claudelocal"].max_concurrent_runs == 1
