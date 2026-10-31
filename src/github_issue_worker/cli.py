"""The command line (F1, F10, F12.8): add, remove, list, workers, watch, once, work, release,
status."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import shutil
import signal
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from github_issue_worker.adapters.control import ControlError, ControlLease, ControlServer
from github_issue_worker.adapters.home import account_home
from github_issue_worker.adapters.installations import EXECUTABLES, portable_tool
from github_issue_worker.adapters.manager_lock import (
    ManagerLockError,
    configuration_identity,
    manager_lock,
)
from github_issue_worker.adapters.workspace import WorkspaceError
from github_issue_worker.application.coordinator import ClaimLost
from github_issue_worker.configuration import (
    AccountSettings,
    Configuration,
    ConfigurationError,
    RepositorySettings,
    WorkerSettings,
    default_path,
    load,
    save,
)
from github_issue_worker.ports.claims import ClaimStoreError
from github_issue_worker.ports.github import GhUnavailable, GitHubError
from github_issue_worker.product import NAME, version

if TYPE_CHECKING:
    from github_issue_worker.wiring import Runtime

_REPOSITORY = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)?(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?(?:/issues/(?P<issue>\d+))?/?$"
)
_PRIORITY = {"claude": 1, "codex": 2, "antigravity": 3, "opencode": 5}
_DRAIN_WAIT_SECONDS = 5.0


class _Host(Protocol):
    def info(self) -> Any: ...
    def ensure_worker_labels(self) -> None: ...


HostFactory = Callable[[str], _Host]
RuntimeFactory = Callable[[Configuration, Path, Path | None, Path | None], "Runtime"]


def normalize_repository(text: str) -> str:
    match = _REPOSITORY.match(text.strip())
    if not match:
        raise ValueError(f"not a GitHub repository: {text!r}")
    return f"{match['owner']}/{match['name']}"


def normalize_issue(text: str) -> tuple[str, int]:
    text = text.strip()
    if "#" in text:
        repository, _, number = text.rpartition("#")
        if number.isdigit():
            return normalize_repository(repository), int(number)
    match = _REPOSITORY.match(text)
    if match and match["issue"]:
        return f"{match['owner']}/{match['name']}", int(match["issue"])
    raise ValueError(f"not an issue reference (owner/repo#n or an issue URL): {text!r}")


# --- worker detection ----------------------------------------------------------------------------


def detect_workers(home: Path) -> list[dict[str, Any]]:
    """One proposed worker per agent installation found: ClaudeCodePortable profiles, ClaudeLocal,
    and the agents on PATH that no portable profile already provides."""
    found: list[dict[str, Any]] = []
    portable = home / "ClaudeCodePortable"
    tools = ("claude", "codex", "antigravity")
    for profile in sorted((portable / "profiles").glob("*")):
        if not profile.is_dir():
            continue
        marker = profile / ".tool"
        kind = marker.read_text(encoding="utf-8").strip() if marker.exists() else "claude"
        if kind in tools and portable_tool(profile, kind).exists():
            found.append(
                {
                    "name": kind,
                    "agent": kind,
                    "account": f"{kind}-{profile.name}",
                    "priority": _PRIORITY[kind],
                    "installation": {
                        "type": "claudecodeportable",
                        "root": str(portable),
                        "profile": profile.name,
                    },
                }
            )

    local = home / "ClaudeLocal"
    if (local / "claude.sh").exists() or (local / "Claude.bat").exists():
        found.append(
            {
                "name": "local",
                "agent": "claude",
                "account": "claudelocal",
                "priority": 9,
                "installation": {"type": "claudelocal", "root": str(local)},
            }
        )

    portable_kinds = {worker["agent"] for worker in found if worker["name"] != "local"}
    for kind, executable in EXECUTABLES.items():
        if kind not in portable_kinds and (shutil.which(executable) or shutil.which(kind)):
            found.append(
                {"name": kind, "agent": kind, "account": kind, "priority": _PRIORITY[kind]}
            )
            found[-1]["installation"] = {"type": "path"}

    names: dict[str, int] = {}
    for worker in found:
        names[worker["name"]] = names.get(worker["name"], 0) + 1
        if names[worker["name"]] > 1:
            worker["name"] = f"{worker['name']}-{names[worker['name']]}"
    return found


def _add_detected_workers(config: Configuration, proposed: Sequence[dict[str, Any]]) -> None:
    existing = {worker.name for worker in config.workers}
    workers, accounts = list(config.workers), dict(config.accounts)
    for raw in proposed:
        if raw["name"] in existing:
            continue
        if raw["account"] not in accounts:
            accounts[raw["account"]] = AccountSettings(max_concurrent_runs=1)
        workers.append(WorkerSettings.model_validate(raw))
    config.workers, config.accounts = workers, accounts


# --- commands ------------------------------------------------------------------------------------


def _add(config: Configuration, path: Path, args: argparse.Namespace, hosts: HostFactory) -> int:
    repository = normalize_repository(args.repository)
    host = hosts(repository)
    if not host.info().can_push:
        print(
            f"{repository}: your account cannot push there, so it cannot be watched",
            file=sys.stderr,
        )
        return 1
    host.ensure_worker_labels()
    if repository not in {r.repository for r in config.repositories}:
        # Assigned, not appended: only assignment marks the key as set, and save writes set keys.
        config.repositories = [*config.repositories, RepositorySettings(repository=repository)]
        save(config, path)
    print(f"watching {repository}")
    return 0


def _control_directory(args: argparse.Namespace, home: Path) -> Path:
    from github_issue_worker.wiring import state_dir

    return args.state_dir or state_dir(home)


def _remove(
    path: Path, args: argparse.Namespace, home: Path, runtime_factory: RuntimeFactory
) -> int:
    from github_issue_worker.adapters.control import remove_or_request

    repository = normalize_repository(args.repository)

    def offline() -> None:
        config = load(path)
        runtime = runtime_factory(config, home, *_dirs(args))
        runtime.remove_repository(repository, path)

    remove_or_request(_control_directory(args, home), path, repository, offline)
    print(f"no longer watching {repository}")
    return 0


def _workers(config: Configuration, path: Path, args: argparse.Namespace, home: Path) -> int:
    if args.detect:
        proposed = detect_workers(home)
        print(json.dumps(proposed, indent=2))
        if args.save:
            _add_detected_workers(config, proposed)
            save(config, path)
        return 0

    from github_issue_worker.adapters.installations import Unavailable, resolve

    for worker in config.workers:
        try:
            resolved = resolve(worker, home)
            state = f"ok ({resolved.executable})"
        except Unavailable as unavailable:
            state = f"unavailable: {unavailable}"
        print(f"{worker.name:14} {worker.agent:12} {worker.account:24} {state}")
    return 0


def _watch(
    config: Configuration,
    args: argparse.Namespace,
    home: Path,
    runtime_factory: RuntimeFactory,
    path: Path | None = None,
    *,
    once: bool = False,
    no_ui: bool = False,
) -> int:
    from github_issue_worker.adapters.process import terminate_all

    first_run = path is not None and not path.exists()
    if first_run and path is not None:
        _add_detected_workers(config, detect_workers(home))
        save(config, path)

    runtime = runtime_factory(config, home, *_dirs(args))
    # The login is proven by the adapter the manager polls through, not by a second `gh` (F0.5).
    try:
        runtime.api.request("GET", "user")
    except GhUnavailable:
        print(
            "gh cannot reach GitHub as your account: run 'gh auth login' and start again",
            file=sys.stderr,
        )
        return 2
    try:
        manager = runtime.manager()
    except WorkspaceError as error:
        print(f"cannot start: {error}", file=sys.stderr)
        return 2
    manager.start()
    stop = threading.Event()

    def interrupt(signum: int, frame: object) -> None:
        if stop.is_set():
            terminate_all()
            raise SystemExit(130)
        print(
            "stopping: no new work is claimed; Ctrl+C again to stop running agents", file=sys.stderr
        )
        stop.set()

    prev_int = signal.signal(signal.SIGINT, interrupt)
    prev_term = signal.signal(signal.SIGTERM, interrupt)
    try:
        control = (
            ControlServer(
                _control_directory(args, home),
                path,
                lambda repository: runtime.remove_repository(repository, path, manager),
            )
            if path is not None
            else contextlib.nullcontext()
        )
        with control:
            count = len(manager.repositories)
            print(f"{NAME} {version()} as {runtime.manager_id}, watching {count} repositories")
            for row in manager.snapshot():
                print(f"  worker {row['worker']}: {'ok' if row['available'] else row['reason']}")
            server = None
            if path is not None and not once and not no_ui:
                server = _start_ui(runtime, manager, path, open_browser=first_run)
            seen = _mtime(path)
            while not stop.is_set():
                try:
                    with getattr(runtime, "configuration_lock", contextlib.nullcontext()):
                        manager.tick()
                    if once or stop.is_set():
                        break
                    stop.wait(runtime.config.poll_interval.total_seconds())
                    seen = _reload_if_changed(runtime, manager, path, seen)
                except KeyboardInterrupt:
                    stop.set()
                    break
            while any(slot.busy for slot in manager.workers):
                stop.wait(_DRAIN_WAIT_SECONDS)
            if server is not None:
                server.stop()
            return 0
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def _mtime(path: Path | None) -> float | None:
    try:
        return path.stat().st_mtime if path is not None else None
    except OSError:
        return None


def _reload_if_changed(
    runtime: Any, manager: Any, path: Path | None, seen: float | None
) -> float | None:
    """A change made in the file while the manager runs is applied like one from the UI (F0.7)."""
    current = _mtime(path)
    if path is None or current == seen:
        return seen
    try:
        runtime.apply(load(path), manager)
        logging.getLogger(__name__).info("configuration reloaded from %s", path)
    except (
        ConfigurationError,
        WorkspaceError,
        ClaimLost,
        ClaimStoreError,
        GitHubError,
        GhUnavailable,
        OSError,
    ) as error:
        logging.getLogger(__name__).error(
            "%s could not be applied, keeping the running configuration: %s", path, error
        )
        return seen
    return current


def _start_ui(runtime: Any, manager: Any, path: Path, open_browser: bool = False) -> Any:
    from github_issue_worker.ui.live import RuntimeBackend
    from github_issue_worker.ui.server import UiServer

    ui = runtime.config.ui
    server = UiServer(RuntimeBackend(runtime, manager, path, detect_workers), ui.bind, ui.port)
    if server.start(wait_for_port=60):
        print(f"  ui: {server.url}")
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(server.url)
        return server
    print(
        f"  ui: {ui.bind}:{ui.port} is still taken; trying again every 15 s in the background",
        file=sys.stderr,
    )
    server.keep_trying(
        on_listening=lambda: logging.getLogger(__name__).info("ui: listening on %s", server.url)
    )
    return server


def _ui(args: argparse.Namespace) -> int:
    from github_issue_worker.ui.demo import DemoBackend
    from github_issue_worker.ui.server import UiServer, capture_screenshots

    if not args.demo:
        print("the UI is part of `watch`; `ui --demo` serves the demo fleet", file=sys.stderr)
        return 2
    if args.screenshots:
        for written in capture_screenshots(DemoBackend(), args.screenshots):
            print(written)
        return 0
    server = UiServer(DemoBackend(), "127.0.0.1", args.port)
    if not server.start():
        print(f"could not listen on port {args.port}", file=sys.stderr)
        return 2
    print(f"demo fleet at {server.url}; Ctrl+C to stop")
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()
    server.stop()
    return 0


def _list(
    config: Configuration, args: argparse.Namespace, home: Path, runtime_factory: RuntimeFactory
) -> int:
    from github_issue_worker.application.repository_listing import RepositoryListing, render_list
    from github_issue_worker.domain.claim import ClaimPolicy

    if not config.repositories:
        print("no repositories watched")
        return 0
    runtime = runtime_factory(config, home, *_dirs(args))
    repositories = []
    for settings in config.repositories:
        watch = runtime.watch(settings)
        issues = tuple(watch.host.open_issues())
        stored = (watch.store.read(number) for number in watch.store.issues())
        repositories.append(
            RepositoryListing(
                settings.repository,
                runtime.state.poll_state(settings.repository).last_polled_at,
                issues,
                tuple(entry.claim for entry in stored if entry),
            )
        )
    fleet = config.fleet
    print(
        render_list(
            repositories,
            ClaimPolicy(fleet.claim_validity, fleet.claim_grace),
            runtime.api.clock.now(),
        ),
        end="",
    )
    return 0


def _status(
    config: Configuration, args: argparse.Namespace, home: Path, runtime_factory: RuntimeFactory
) -> int:
    from github_issue_worker.application.status import RepositoryClaims, render_status
    from github_issue_worker.domain.claim import ClaimPolicy

    runtime = runtime_factory(config, home, *_dirs(args))
    repositories = []
    for settings in config.repositories:
        store = runtime.watch(settings).store
        stored = (store.read(number) for number in store.issues())
        claims = tuple(entry.claim for entry in stored if entry)
        repositories.append(RepositoryClaims(settings.repository, claims))
    fleet = config.fleet
    print(
        render_status(
            runtime.manager_id,
            runtime.slots,
            runtime.state.limited_until,
            repositories,
            ClaimPolicy(fleet.claim_validity, fleet.claim_grace),
            runtime.api.clock.now(),
            tuple(config.accounts),
        ),
        end="",
    )
    return 0


def _release(config: Configuration, args: argparse.Namespace, home: Path) -> int:
    from github_issue_worker.application.coordinator import ClaimHandle
    from github_issue_worker.wiring import build_runtime

    repository, number = normalize_issue(args.issue)
    runtime = build_runtime(config, home, *_dirs(args))
    settings = next((r for r in config.repositories if r.repository == repository), None)
    watch = runtime.watch(settings or RepositorySettings(repository=repository))
    stored = watch.store.read(number)
    if stored is None or not stored.claim.state.holds:
        print(f"{repository}#{number} holds no claim")
        return 0
    if stored.claim.holder.manager != runtime.manager_id and not args.force:
        print(
            f"claimed by {stored.claim.holder}; use --force to release another manager's claim",
            file=sys.stderr,
        )
        return 1
    watch.coordinator.release(ClaimHandle(stored.version, stored.claim), step="released")
    print(f"released {repository}#{number}")
    return 0


def _work(config: Configuration, args: argparse.Namespace, home: Path) -> int:
    from github_issue_worker.application.manager import branch_name
    from github_issue_worker.domain.claim import Holder
    from github_issue_worker.domain.work import RunKind
    from github_issue_worker.wiring import build_runtime

    repository, number = normalize_issue(args.issue)
    runtime = build_runtime(config, home, *_dirs(args))
    settings = next((r for r in config.repositories if r.repository == repository), None)
    watch = runtime.watch(settings or RepositorySettings(repository=repository))
    slots = [s for s in runtime.slots if s.available and (args.worker in (None, s.name))]
    if not slots:
        print("no available worker", file=sys.stderr)
        return 1
    slot = min(slots, key=lambda s: s.priority)
    issue = watch.host.issue(number)
    stored = watch.store.read(number)
    branch = stored.claim.branch if stored else branch_name(issue)
    handle = watch.coordinator.claim(number, Holder(runtime.manager_id, slot.name), branch)
    if handle is None:
        print(f"{repository}#{number} is claimed by someone else", file=sys.stderr)
        return 1
    kind = RunKind.CONTINUATION if stored else RunKind.FIRST
    comments = tuple(watch.host.comments(number))
    verdict = runtime.run_issue(watch, slot, handle, issue, comments, kind)
    print(f"{repository}#{number}: {verdict.kind} {verdict.detail}")
    return 0 if verdict.kind in ("merged", "needs-input", "awaiting-review", "paused") else 1


def _build_runtime() -> RuntimeFactory:
    from github_issue_worker.wiring import build_runtime

    return build_runtime


def _dirs(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    """Explicit directories, else the configuration's own; the default one keeps the base dirs."""
    from github_issue_worker.wiring import cache_dir, state_dir

    path, home = args.config_path, args.home
    scope = None
    if configuration_identity(path) != configuration_identity(default_path(home=home)):
        scope = Path("configurations") / configuration_identity(path)[:16]
    state = args.state_dir / "state.json" if args.state_dir else None
    if state is None and scope is not None:
        state = state_dir(home) / scope / "state.json"
    cache = args.cache_dir or (cache_dir(home) / scope if scope is not None else None)
    return state, cache


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="github-issue-worker", description=f"{NAME} {version()}")
    parser.add_argument("-V", "--version", action="version", version=f"{NAME} {version()}")
    parser.add_argument("--config", type=Path, help="configuration file (default: platform path)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--state-dir", type=Path, help="local state (default: ~/.local/state/...)")
    parser.add_argument(
        "--cache-dir", type=Path, help="clones and worktrees (default: ~/.cache/...)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="watch a repository you can push to")
    add.add_argument("repository")
    remove = commands.add_parser(
        "remove", help="checkpoint local work, release claims and stop watching"
    )
    remove.add_argument("repository")
    commands.add_parser(
        "list", help="watched repositories: last poll, state counts and current issues"
    )

    workers = commands.add_parser("workers", help="list workers, or detect agent installations")
    workers.add_argument(
        "--detect", action="store_true", help="propose one worker per installation"
    )
    workers.add_argument(
        "--save", action="store_true", help="with --detect: add them to the config"
    )

    ownership_help = (
        "Only one manager may run per configuration on this machine. "
        "A duplicate exits with a diagnostic containing the existing manager PID."
    )
    watch = commands.add_parser(
        "watch", help="run the manager until stopped", description=ownership_help
    )
    watch.add_argument("--once", action="store_true", help="one poll-and-work pass, then exit")
    watch.add_argument("--no-ui", action="store_true", help="do not serve the local UI")
    watch.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    watch.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    watch.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    watch.add_argument("--cache-dir", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    once = commands.add_parser(
        "once", help="one poll-and-work pass, then exit (for cron)", description=ownership_help
    )
    once.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    once.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    once.add_argument("--state-dir", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    once.add_argument("--cache-dir", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    ui = commands.add_parser("ui", help="serve the demo fleet, or capture its screenshots")
    ui.add_argument("--demo", action="store_true", help="serve fixed, believable sample data")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--screenshots", type=Path, help="write a PNG of every page to this directory")

    work = commands.add_parser("work", help="work one issue now")
    work.add_argument("issue", help="owner/repo#n or an issue URL")
    work.add_argument("--worker", help="the worker to use (default: the preferred available one)")

    release = commands.add_parser("release", help="release a claim with a checkpoint")
    release.add_argument("issue")
    release.add_argument("--force", action="store_true", help="also another manager's claim")

    commands.add_parser("status", help="workers, accounts and claims")
    return parser


def main(
    argv: Sequence[str] | None = None,
    host_factory: HostFactory | None = None,
    home: Path | None = None,
    runtime_factory: RuntimeFactory | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    home = home or account_home()
    path = args.config or default_path(home=home)
    args.config_path, args.home = path, home
    try:
        if args.command in {"watch", "once"}:
            with manager_lock(path) as target:
                # Use the exact target locked, even if a symlink is retargeted during startup.
                return _run_command(args, parser, target, home, host_factory, runtime_factory)
        return _run_command(args, parser, path, home, host_factory, runtime_factory)
    except ManagerLockError as error:
        print(str(error), file=sys.stderr)
        return 2


def _run_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    path: Path,
    home: Path,
    host_factory: HostFactory | None,
    runtime_factory: RuntimeFactory | None,
) -> int:
    try:
        config = load(path)
    except ConfigurationError as error:
        for problem in error.problems:
            print(f"{path}: {problem.path}: {problem.message}", file=sys.stderr)
        return 2

    def hosts(repository: str) -> _Host:
        from github_issue_worker.adapters.gh_api import GhApi
        from github_issue_worker.application.github_host import GitHubHost

        return GitHubHost(GhApi(home=home), repository)

    try:
        if args.command == "add":
            return _add(config, path, args, host_factory or hosts)
        if args.command == "remove":
            return _remove(path, args, home, runtime_factory or _build_runtime())
        if args.command == "list":
            return _list(config, args, home, runtime_factory or _build_runtime())
        if args.command == "workers":
            return _workers(config, path, args, home)
        if args.command == "watch":
            with ControlLease(_control_directory(args, home)):
                return _watch(
                    config,
                    args,
                    home,
                    runtime_factory or _build_runtime(),
                    path,
                    once=args.once,
                    no_ui=args.no_ui,
                )
        if args.command == "once":
            with ControlLease(_control_directory(args, home)):
                return _watch(
                    config,
                    args,
                    home,
                    runtime_factory or _build_runtime(),
                    path,
                    once=True,
                    no_ui=True,
                )
        if args.command == "ui":
            return _ui(args)
        if args.command == "status":
            return _status(config, args, home, runtime_factory or _build_runtime())
        if args.command == "release":
            return _release(config, args, home)
        if args.command == "work":
            with ControlLease(_control_directory(args, home)):
                return _work(config, args, home)
    except (
        ValueError,
        ConfigurationError,
        ControlError,
        WorkspaceError,
        ClaimLost,
        ClaimStoreError,
        GitHubError,
        GhUnavailable,
        OSError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 2
    parser.error(f"unknown command {args.command}")
    return 2
