from __future__ import annotations

import contextlib
import json
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from github_issue_worker.adapters.installations import portable_tool
from github_issue_worker.application.manager import WorkerSlot
from github_issue_worker.cli import detect_workers, main, normalize_issue, normalize_repository
from github_issue_worker.configuration import load
from github_issue_worker.domain.claim import Claim, ClaimPolicy, ClaimState, Holder
from github_issue_worker.ports.claims import StoredClaim
from github_issue_worker.ports.github import GhUnavailable, Response
from github_issue_worker.ports.repository import RepositoryInfo


@pytest.fixture(autouse=True)
def fake_local_control(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_issue_worker.cli as cli

    monkeypatch.setattr(cli, "ControlLease", lambda *args: contextlib.nullcontext())
    monkeypatch.setattr(cli, "ControlServer", lambda *args: contextlib.nullcontext())


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


def test_remove_and_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import github_issue_worker.adapters.control as control
    from github_issue_worker.application.poller import PollState
    from github_issue_worker.configuration import save
    from tests.fakes import FakeClock, InMemoryClaimStore

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"repositories": [{"repository": "o/a"}, {"repository": "o/b"}]}))

    def remove(repository: str, path: Path) -> None:
        current = load(path)
        current.repositories = [r for r in current.repositories if r.repository != repository]
        save(current, path)

    runtime = SimpleNamespace(
        remove_repository=remove,
        watch=lambda settings: SimpleNamespace(
            host=SimpleNamespace(open_issues=lambda: []), store=InMemoryClaimStore()
        ),
        state=SimpleNamespace(poll_state=lambda repo: PollState()),
        api=SimpleNamespace(clock=FakeClock(datetime(2026, 10, 31, tzinfo=UTC))),
    )
    monkeypatch.setattr(
        control, "remove_or_request", lambda directory, path, repo, offline: offline()
    )
    assert (
        main(["--config", str(config), "remove", "o/a"], runtime_factory=lambda *args: runtime) == 0
    )
    capsys.readouterr()
    assert main(["--config", str(config), "list"], runtime_factory=lambda *args: runtime) == 0
    out = capsys.readouterr().out
    assert "o/b" in out and "o/a" not in out and "no current issues" in out


class Api:
    """The GitHub adapter as the CLI sees it: every request recorded, answered, or refused the way
    an unlogged-in `gh` refuses — no HTTP response at all."""

    def __init__(self, logged_in: bool = True) -> None:
        self.logged_in = logged_in
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_: object) -> Response:
        if path != "user/repos":
            self.requests.append((method, path))
        if not self.logged_in:
            raise GhUnavailable("To get started with GitHub CLI, please run:  gh auth login")
        return Response(status=200, body=[] if path == "user/repos" else {"login": "hawkynt"})


class Watch:
    def __init__(self, host: Host) -> None:
        self.host = host


class Manager:
    def __init__(self, hosts: list[Host] | None = None) -> None:
        self.repositories: list[Watch] = [Watch(host) for host in hosts or []]
        self.workers: list[object] = []
        self.ticks = 0

    def start(self) -> None:
        for watch in self.repositories:
            watch.host.ensure_worker_labels()

    def snapshot(self) -> list[dict[str, object]]:
        return []

    def tick(self) -> None:
        self.ticks += 1


class State:
    def __init__(self, paused: bool = False) -> None:
        self._paused = paused

    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        self._paused = paused


class Runtime:
    def __init__(self, api: Api, manager: Manager, state: State | None = None) -> None:
        self.api = api
        self.manager_id = "manager@host"
        self._manager = manager
        self.state = state or State()

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


def run_once(config: Path, api: Api, manager: Manager) -> int:
    return main(
        ["--config", str(config), "once"],
        home=config.parent,
        runtime_factory=lambda *a: Runtime(api, manager),
    )


def test_given_a_watched_config_when_once_runs_then_it_polls_once_and_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(can_push=True)
    api, manager = Api(), Manager([host])
    assert run_once(watched_config(tmp_path), api, manager) == 0
    assert api.requests == [("GET", "user")]
    assert host.labelled
    assert manager.ticks == 1
    assert "watching 1 repositories" in capsys.readouterr().out


def test_given_gh_cannot_reach_github_when_once_runs_then_it_stops_with_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(can_push=True)
    api, manager = Api(logged_in=False), Manager([host])
    assert run_once(watched_config(tmp_path), api, manager) == 2
    assert "gh auth login" in capsys.readouterr().err
    assert api.requests == [("GET", "user")]
    assert manager.ticks == 0
    assert not host.labelled


def test_given_ui_defaults_on_when_once_runs_then_it_starts_no_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import github_issue_worker.ui.server as ui_server

    started: list[tuple[str, int]] = []

    class FakeServer:
        def __init__(self, backend: object, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.url = f"http://localhost:{port}"

        def start(self, **_: object) -> bool:
            started.append((self.host, self.port))
            return True

        def stop(self) -> None:
            pass

    class ConfiguredRuntime(Runtime):
        def __init__(self, cfg: object, api: Api, manager: Manager) -> None:
            super().__init__(api, manager)
            self.config = cfg

    monkeypatch.setattr(ui_server, "UiServer", FakeServer)
    code = main(
        ["--config", str(watched_config(tmp_path)), "once"],
        home=tmp_path,
        runtime_factory=lambda cfg, *_: ConfiguredRuntime(cfg, Api(), Manager()),
    )
    assert code == 0
    assert started == []


def test_given_a_busy_worker_after_the_pass_when_once_runs_then_it_waits_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import github_issue_worker.cli as cli

    monkeypatch.setattr(cli, "_DRAIN_WAIT_SECONDS", 0.01)

    class BusySlot:
        def __init__(self) -> None:
            self.polls = 0

        @property
        def busy(self) -> bool:
            self.polls += 1
            return self.polls < 3

    class BusyManager(Manager):
        def __init__(self) -> None:
            super().__init__()
            self.slot = BusySlot()
            self.workers = [self.slot]

    manager = BusyManager()
    assert run_once(watched_config(tmp_path), Api(), manager) == 0
    assert manager.ticks == 1
    assert manager.slot.polls >= 2  # waited for the worker instead of exiting on it


def test_given_no_config_when_once_starts_then_it_detects_workers_of_one_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("claude", "codex", "antigravity"):
        tool(tmp_path / "bin" / (f"{name}.cmd" if sys.platform == "win32" else name))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    config_path = tmp_path / "config.json"
    assert (
        main(
            ["--config", str(config_path), "once"],
            home=tmp_path,
            runtime_factory=lambda *a: Runtime(Api(), Manager()),
        )
        == 0
    )
    saved = load(config_path)
    assert {(w.name, w.agent, w.installation.type) for w in saved.workers} == {
        ("claude", "claude", "path"),
        ("codex", "codex", "path"),
        ("antigravity", "antigravity", "path"),
    }
    assert set(saved.accounts) == {"claude", "codex", "antigravity"}


def tool(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_detection_finds_portable_profiles_claudelocal_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    portable = tmp_path / "ClaudeCodePortable" / "profiles"
    tool(portable_tool(portable / "default", "claude"))
    tool(portable_tool(portable / "ChatGPTCodex", "codex"))
    (portable / "ChatGPTCodex" / ".tool").write_text("codex", encoding="utf-8")
    tool(portable_tool(portable / "GoogleGemini", "antigravity"))
    (portable / "GoogleGemini" / ".tool").write_text("antigravity", encoding="utf-8")
    tool(tmp_path / "ClaudeLocal" / ("Claude.bat" if sys.platform == "win32" else "claude.sh"))
    tool(tmp_path / "bin" / ("opencode.cmd" if sys.platform == "win32" else "opencode"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    found = {(w["name"], w["agent"], w["installation"]["type"]) for w in detect_workers(tmp_path)}
    assert found == {
        ("claude", "claude", "claudecodeportable"),
        ("codex", "codex", "claudecodeportable"),
        ("antigravity", "antigravity", "claudecodeportable"),
        ("local", "claude", "claudelocal"),
        ("opencode", "opencode", "path"),
    }


def test_given_a_configured_portable_root_when_detecting_then_its_profiles_are_proposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    root = tmp_path / "Agents" / "ClaudePortable"
    tool(portable_tool(root / "profiles" / "me", "claude"))
    (root / "profiles" / "me" / ".tool").write_text("claude", encoding="utf-8")
    tool(portable_tool(root / "profiles" / "codex", "codex"))
    (root / "profiles" / "codex" / ".tool").write_text("codex", encoding="utf-8")
    (root / "profiles" / "copilot").mkdir()
    (root / "profiles" / "copilot" / ".tool").write_text("copilot", encoding="utf-8")

    found = detect_workers(tmp_path, [str(root)])

    assert {(w["agent"], w["installation"]["profile"]) for w in found} == {
        ("claude", "me"),
        ("codex", "codex"),
    }
    assert {w["installation"]["root"] for w in found} == {str(root)}
    assert detect_workers(tmp_path) == []


def test_given_portable_roots_in_the_config_when_workers_detect_runs_then_they_are_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    root = tmp_path / "Agents" / "ClaudePortable"
    tool(portable_tool(root / "profiles" / "me", "claude"))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"manager": {"portableRoots": [str(root)]}}), encoding="utf-8")

    assert main(["--config", str(config), "workers", "--detect", "--save"], home=tmp_path) == 0

    saved = load(config)
    assert [(w.name, w.installation.type) for w in saved.workers] == [
        ("claude", "claudecodeportable")
    ]


def test_workers_detect_save_writes_workers_and_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    tool(tmp_path / "ClaudeLocal" / "claude.sh")
    config = tmp_path / "config.json"
    assert main(["--config", str(config), "workers", "--detect", "--save"], home=tmp_path) == 0
    saved = load(config)
    assert [w.name for w in saved.workers] == ["local"]
    assert set(saved.accounts) == {"claudelocal"}
    assert saved.workers[0].enabled is True  # added on purpose


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 30, 10, 0, tzinfo=UTC)


class Store:
    def __init__(self, claims: list[Claim]) -> None:
        self.claims = {claim.issue: claim for claim in claims}

    def issues(self) -> list[int]:
        return sorted(self.claims)

    def read(self, issue: int) -> StoredClaim | None:
        claim = self.claims.get(issue)
        return StoredClaim("v1", claim) if claim else None


class Limits:
    def limited_until(self, account: str) -> datetime | None:
        return None


class StatusRuntime:
    def __init__(self, claims: list[Claim]) -> None:
        self.manager_id = "manager@host"
        self.slots = [WorkerSlot(name="alpha", agent="claude", account="personal")]
        self.state = Limits()
        self.api = SimpleNamespace(clock=Clock())
        self._store = Store(claims)

    def watch(self, settings: object) -> SimpleNamespace:
        return SimpleNamespace(store=self._store)


def run_status(config: Path, runtime: StatusRuntime) -> int:
    return main(
        ["--config", str(config), "status"],
        home=config.parent,
        runtime_factory=lambda *a: runtime,  # type: ignore[arg-type,return-value]
    )


def test_status_prints_the_claims_of_each_watched_repository_by_holder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = Clock().now()
    claim = ClaimPolicy(timedelta(days=7), timedelta(days=1)).new_claim(
        12, Holder("manager@host", "alpha"), now - timedelta(minutes=5), "issue/12-x"
    )
    done = claim.replace(issue=3, state=ClaimState.DONE)
    assert run_status(watched_config(tmp_path), StatusRuntime([claim, done])) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[:2] == ["Manager", "  manager@host"]
    assert "  Claims held by this manager" in out
    row = " ".join(next(line for line in out.splitlines() if "#12" in line).split())
    assert row == "#12 alpha - working triage 5m ago 2026-11-06 09:55 UTC (in 6d 23h) issue/12-x"
    assert "  1 finished (done/released)" in out


def test_status_without_watched_repositories_points_to_add(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.json"
    config.write_text("{}")
    assert run_status(config, StatusRuntime([])) == 0
    assert "no repositories watched; add one with 'github-issue-worker add <owner/repo>'" in (
        capsys.readouterr().out
    )


def test_watch_explains_a_missing_git_identity_instead_of_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Seen live: the account home had no global git identity, and watch died with a traceback.
    from github_issue_worker.adapters.workspace import WorkspaceError

    class NoIdentity(Runtime):
        def manager(self) -> Manager:
            raise WorkspaceError("no git identity: set user.name and user.email")

    code = main(
        ["--config", str(watched_config(tmp_path)), "watch", "--once"],
        home=tmp_path,
        runtime_factory=lambda *a: NoIdentity(Api(), Manager()),
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "no git identity" in err and "Traceback" not in err


def test_given_no_config_when_watch_starts_then_it_detects_workers_of_one_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("claude", "codex", "antigravity"):
        tool(tmp_path / "bin" / (f"{name}.cmd" if sys.platform == "win32" else name))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    config_path = tmp_path / "config.json"
    observed: list[object] = []

    def factory(cfg: object, *_: object) -> Runtime:
        observed.append(cfg)
        return Runtime(Api(), Manager())

    assert (
        main(
            ["--config", str(config_path), "watch", "--once"],
            home=tmp_path,
            runtime_factory=factory,  # type: ignore[arg-type]
        )
        == 0
    )
    assert config_path.exists()
    saved = load(config_path)
    assert {(w.name, w.agent, w.installation.type) for w in saved.workers} == {
        ("claude", "claude", "path"),
        ("codex", "codex", "path"),
        ("antigravity", "antigravity", "path"),
    }
    assert set(saved.accounts) == {"claude", "codex", "antigravity"}
    assert len(observed) == 1
    assert len(observed[0].workers) == 3  # type: ignore[attr-defined]
    # Found, not chosen: nothing runs until the user switches a worker on.
    assert {w.enabled for w in saved.workers} == {False}


def test_given_no_config_when_watch_starts_then_it_starts_paused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    state = State()
    code = main(
        ["--config", str(tmp_path / "config.json"), "watch", "--once"],
        home=tmp_path,
        runtime_factory=lambda *a: Runtime(Api(), Manager(), state),
    )
    assert code == 0 and state.paused() is True
    assert "paused" in capsys.readouterr().out


@pytest.mark.parametrize(("flag", "expected"), [([], False), (["--paused"], True)])
def test_given_a_config_when_watch_starts_then_only_the_flag_pauses_it(
    tmp_path: Path, flag: list[str], expected: bool
) -> None:
    state = State()
    code = main(
        ["--config", str(watched_config(tmp_path)), "watch", "--once", *flag],
        home=tmp_path,
        runtime_factory=lambda *a: Runtime(Api(), Manager(), state),
    )
    assert code == 0 and state.paused() is expected


def test_given_a_paused_state_when_watch_starts_without_the_flag_then_it_stays_paused(
    tmp_path: Path,
) -> None:
    state = State(paused=True)
    main(
        ["--config", str(watched_config(tmp_path)), "watch", "--once"],
        home=tmp_path,
        runtime_factory=lambda *a: Runtime(Api(), Manager(), state),
    )
    assert state.paused() is True


def test_given_no_config_when_watch_starts_with_ui_then_it_opens_web_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    import github_issue_worker.ui.server as ui_server

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    config_path = tmp_path / "config.json"
    opened: list[str] = []
    started: list[tuple[str, int]] = []

    class FakeServer:
        def __init__(self, backend: object, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.url = f"http://localhost:{port}"

        def start(self, **_: object) -> bool:
            started.append((self.host, self.port))
            return True

        def stop(self) -> None:
            pass

    class StoppingManager(Manager):
        def tick(self) -> None:
            super().tick()
            raise KeyboardInterrupt

    class ConfiguredRuntime(Runtime):
        def __init__(self, cfg: object, api: Api, manager: Manager) -> None:
            super().__init__(api, manager)
            self.config = cfg

    monkeypatch.setattr(ui_server, "UiServer", FakeServer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    assert (
        main(
            ["--config", str(config_path), "watch"],
            home=tmp_path,
            runtime_factory=lambda cfg, *_: ConfiguredRuntime(cfg, Api(), StoppingManager()),
        )
        == 0
    )
    assert config_path.exists()
    assert started == [("127.0.0.1", 8765)]
    assert opened == ["http://localhost:8765"]


def test_given_existing_config_when_watch_starts_then_it_keeps_config_without_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import webbrowser

    import github_issue_worker.ui.server as ui_server

    tool(tmp_path / "bin" / ("claude.cmd" if sys.platform == "win32" else "claude"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    config_path = watched_config(tmp_path)
    opened: list[str] = []

    class FakeServer:
        def __init__(self, backend: object, host: str, port: int) -> None:
            self.url = f"http://localhost:{port}"

        def start(self, **_: object) -> bool:
            return True

        def stop(self) -> None:
            pass

    class StoppingManager(Manager):
        def tick(self) -> None:
            super().tick()
            raise KeyboardInterrupt

    class ConfiguredRuntime(Runtime):
        def __init__(self, cfg: object, api: Api, manager: Manager) -> None:
            super().__init__(api, manager)
            self.config = cfg

    monkeypatch.setattr(ui_server, "UiServer", FakeServer)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)

    assert (
        main(
            ["--config", str(config_path), "watch"],
            home=tmp_path,
            runtime_factory=lambda cfg, *_: ConfiguredRuntime(cfg, Api(), StoppingManager()),
        )
        == 0
    )
    assert load(config_path).workers == []
    assert opened == []


@pytest.mark.parametrize("allowed", [True, False])
def test_given_watch_once_when_starting_then_check_access_before_creating_labels(
    tmp_path: Path,
    allowed: bool,
) -> None:
    from unittest.mock import Mock

    from tests.application.test_manager import Fixture

    f = Fixture([])
    calls: list[str] = []
    f.host.info = Mock(
        side_effect=lambda: calls.append("access") or RepositoryInfo("main", allowed, ("squash",))
    )
    f.host.ensure_worker_labels = lambda: calls.append("labels")  # type: ignore[method-assign]
    assert run_watch(watched_config(tmp_path), Api(), f.manager) == 0  # type: ignore[arg-type]
    assert calls == (["access", "labels"] if allowed else ["access"])
