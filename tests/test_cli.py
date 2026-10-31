from __future__ import annotations

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


def test_given_claims_and_unserved_issues_when_status_runs_then_it_prints_fleet_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from github_issue_worker.domain.issue import Actor, Issue

    now = Clock().now()
    policy = ClaimPolicy(timedelta(days=7), timedelta(days=1))
    mine = policy.new_claim(
        12,
        Holder("manager@host", "alpha"),
        now - timedelta(minutes=5),
        "issue/12-x",
        model="claude-opus-5-5",
    )
    remote = policy.new_claim(
        18, Holder("other@box", "codex"), now - timedelta(hours=1), "issue/18-y"
    ).replace(state=ClaimState.PAUSED, paused_until=now + timedelta(hours=3))
    unserved_issue = Issue(
        number=25,
        title="Needs GPU worker",
        body="",
        state="open",
        author=Actor("hawkynt"),
        labels=frozenset({"worker:unserved", "needs:gpu"}),
        is_pull_request=False,
        created_at=now,
        updated_at=now,
    )
    served_issue = Issue(
        number=12,
        title="Working issue",
        body="",
        state="open",
        author=Actor("hawkynt"),
        labels=frozenset({"worker:working"}),
        is_pull_request=False,
        created_at=now,
        updated_at=now,
    )
    runtime = StatusRuntime([mine, remote])
    runtime.slots = [
        WorkerSlot(
            name="alpha",
            agent="claude",
            account="personal",
            observed_model="claude-opus-5-5",
        )
    ]
    runtime.watch = lambda settings: SimpleNamespace(  # type: ignore[method-assign]
        store=runtime._store,
        host=SimpleNamespace(open_issues=lambda: [served_issue, unserved_issue]),
    )
    assert run_status(watched_config(tmp_path), runtime) == 0
    out = capsys.readouterr().out
    assert "  Known managers: manager@host, other@box" in out
    assert "Claude Opus 5.5" in out
    assert "paused until 2026-10-30 13:00 UTC (in 3h 0m)" in out
    assert "  Unserved issues" in out
    assert "#25" in out and "needs:gpu" in out and "Needs GPU worker" in out


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


def test_given_a_posix_checkout_when_run_sh_is_executed_then_it_runs_watch_with_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    run_sh = repo_root / "run.sh"
    assert run_sh.exists()
    text = run_sh.read_text(encoding="utf-8")
    assert "-m venv" in text
    assert "pip" in text and "install" in text
    assert 'github-issue-worker" watch "$@"' in text

    if sys.platform != "win32":
        shutil.copy2(run_sh, tmp_path / "run.sh")
        fake_python = tmp_path / "bin" / "python3"
        fake_python.parent.mkdir(parents=True, exist_ok=True)
        fake_python.write_text(
            "#!/bin/sh\n"
            'mkdir -p "$3/bin"\n'
            'printf "#!/bin/sh\\n" > "$3/bin/python"\n'
            'chmod +x "$3/bin/python"\n'
            "cat > \"$3/bin/pip\" <<'EOF'\n"
            "#!/bin/sh\n"
            'VENV_BIN="$(dirname "$0")"\n'
            "cat > \"$VENV_BIN/github-issue-worker\" <<'INNER'\n"
            "#!/bin/sh\n"
            'printf "%s\\n" "$@" > "$(dirname "$0")/../../invoked.txt"\n'
            "INNER\n"
            'chmod +x "$VENV_BIN/github-issue-worker"\n'
            "EOF\n"
            'chmod +x "$3/bin/pip"\n',
            encoding="utf-8",
        )
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PYTHON", str(fake_python))

        result = subprocess.run(
            ["bash", str(tmp_path / "run.sh"), "--once", "--no-ui"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "invoked.txt").read_text(encoding="utf-8").splitlines() == [
            "watch",
            "--once",
            "--no-ui",
        ]


def test_given_a_windows_checkout_when_run_bat_is_executed_then_it_runs_watch_with_arguments(
    tmp_path: Path,
) -> None:
    import shutil
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    run_bat = repo_root / "run.bat"
    assert run_bat.exists()
    text = run_bat.read_text(encoding="utf-8")
    assert "python -m venv" in text
    assert "pip" in text and "install" in text
    assert "github-issue-worker" in text and "watch %*" in text

    if sys.platform == "win32":
        shutil.copy2(run_bat, tmp_path / "run.bat")
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "github-issue-worker.cmd").write_text(
            "@echo off\necho %* > invoked.txt\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(tmp_path / "run.bat"), "--once", "--no-ui"],
            cwd=tmp_path,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "watch --once --no-ui" in (tmp_path / "invoked.txt").read_text(encoding="utf-8")


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
    assert {name: acc.max_concurrent_runs for name, acc in saved.accounts.items()} == {
        "claude": 1,
        "codex": 1,
        "antigravity": 1,
    }
    assert len(observed) == 1
    assert len(observed[0].workers) == 3  # type: ignore[attr-defined]


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
