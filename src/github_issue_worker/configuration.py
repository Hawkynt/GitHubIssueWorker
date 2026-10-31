"""The manager's configuration (F0.3): one schema shared by the file, the CLI and the UI.

Keys are camelCase in the file and snake_case in Python. Unknown keys are errors. Every problem is
reported with its JSON path, and saving writes only what was explicitly set, atomically.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
    model_validator,
)
from pydantic.alias_generators import to_camel

from github_issue_worker.domain.commit_message import DEFAULT_FORBIDDEN_WORDS

_DURATION = re.compile(
    r"^(?:(?P<days>\d+)\.)?(?P<hours>\d{1,2}):(?P<minutes>\d{2}):(?P<seconds>\d{2})$"
)


def _parse_duration(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = _DURATION.match(value)
    if not match:
        raise ValueError("expected a duration as [days.]hh:mm:ss")
    hours, minutes, seconds = (int(match[name]) for name in ("hours", "minutes", "seconds"))
    if hours > 23 or minutes > 59 or seconds > 59:
        raise ValueError("hours must be below 24, minutes and seconds below 60")
    return timedelta(days=int(match["days"] or 0), hours=hours, minutes=minutes, seconds=seconds)


def _format_duration(value: timedelta) -> str:
    total = int(value.total_seconds())
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, seconds = divmod(rest, 60)
    clock = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{days}.{clock}" if days else clock


Duration = Annotated[
    timedelta,
    BeforeValidator(_parse_duration),
    PlainSerializer(_format_duration, return_type=str, when_used="json"),
]
"""A ``[days.]hh:mm:ss`` duration, e.g. ``00:05:00`` or ``7.00:00:00``."""

AgentKind = Literal["claude", "codex", "antigravity", "opencode", "command", "remote"]
RepositoryName = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")]


class _Settings(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    def is_set(self, field: str) -> bool:
        """Whether ``field`` was given explicitly rather than taken from its default."""
        return field in self.model_fields_set


class ManagerSettings(_Settings):
    name: str | None = None
    tags: list[str] = Field(default_factory=list)
    home: str | None = None
    """Home directory for gh and networked git. Default: the account's home from the operating
    system, so a HOME relocated by ClaudeCodePortable or ClaudeLocal does not hide gh's login."""


class UiSettings(_Settings):
    port: int = Field(default=8765, ge=1, le=65_535)
    bind: str = "127.0.0.1"
    token: str | None = None


MAX_CONCURRENT_RUNS = 20
"""The most issues one account's workers may run at once on one manager; there is no unlimited."""


class AccountSettings(_Settings):
    max_concurrent_runs: int = Field(default=1, ge=1, le=MAX_CONCURRENT_RUNS)


class HueSegment(_Settings):
    """A maker's part of the colour wheel: its centre degree and its width, both in OKLCH hues."""

    hue: int = Field(default=0, ge=0, le=359)
    width: int = Field(default=40, ge=0, le=360)


class ModelColorsSettings(_Settings):
    """The hue segment every model of one maker is drawn in (F14.19)."""

    anthropic: HueSegment = Field(default_factory=lambda: HueSegment(hue=70, width=90))
    openai: HueSegment = Field(default_factory=lambda: HueSegment(hue=250, width=55))
    google: HueSegment = Field(default_factory=lambda: HueSegment(hue=155, width=55))
    other: HueSegment = Field(default_factory=lambda: HueSegment(hue=320, width=120))
    """Makers not named above share this segment, each keeping a fixed part of it."""

    @model_validator(mode="before")
    @classmethod
    def _keep_maker_defaults(cls, data: Any) -> Any:
        """A segment that sets only its hue or only its width keeps its maker's default for the
        other, never ``HueSegment``'s own: a saved width alone must not turn the hue into 0."""
        if not isinstance(data, dict):
            return data
        defaults = cls.model_construct()
        completed = dict(data)
        for name, info in cls.model_fields.items():
            for key in {name, info.alias or name}:
                given = completed.get(key)
                if isinstance(given, dict):
                    completed[key] = {**getattr(defaults, name).model_dump(), **given}
        return completed


class PathInstallation(_Settings):
    type: Literal["path"] = "path"


class ExecutableInstallation(_Settings):
    type: Literal["executable"]
    path: str
    env: dict[str, str] = Field(default_factory=dict)


class ClaudeCodePortableInstallation(_Settings):
    type: Literal["claudecodeportable"]
    root: str = "~/ClaudeCodePortable"
    profile: str = "default"


class ClaudeLocalInstallation(_Settings):
    type: Literal["claudelocal"]
    root: str = "~/ClaudeLocal"


Installation = Annotated[
    PathInstallation
    | ExecutableInstallation
    | ClaudeCodePortableInstallation
    | ClaudeLocalInstallation,
    Field(discriminator="type"),
]

_PORTABLE_AGENTS = frozenset({"claude", "codex", "antigravity"})


class WorkerSettings(_Settings):
    name: str = Field(min_length=1)
    agent: AgentKind
    account: str
    installation: Installation = Field(default_factory=PathInstallation)
    priority: int = 100
    tags: list[str] = Field(default_factory=list)
    model: str | None = None
    max_budget_usd_per_run: float | None = Field(default=None, gt=0)
    command: list[str] | None = None
    limit_patterns: list[str] = Field(default_factory=list)


class FleetSettings(_Settings):
    claim_validity: Duration = timedelta(days=7)
    claim_grace: Duration = timedelta(days=1)
    checkpoint_interval: Duration = timedelta(minutes=5)
    release_broken_after: Duration = timedelta(hours=1)
    max_takeovers: int = Field(default=3, ge=1)
    stop_claiming_at: float = Field(default=0.9, ge=0, le=1)
    local_hand_off: bool = True
    hand_off_when_paused_longer_than: Duration | None = None


class DefaultsSettings(_Settings):
    mode: Literal["all", "labeled"] = "all"
    base_branch: str | None = None
    merge_method: Literal["squash", "merge", "rebase"] = "squash"
    ci_repair_attempts: int = Field(default=3, ge=0)
    process_backlog: bool = False
    create_missing_labels: bool = False
    max_active_issues_per_repository: int = Field(default=4, ge=1)
    run_timeout: Duration = timedelta(hours=2)
    checks_timeout: Duration = timedelta(hours=2)
    build: str | None = None
    test: str | None = None
    forbidden_words: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_WORDS))
    max_prose_lines: int = Field(default=2, ge=0)
    split_issues: bool = True


class RepositorySettings(_Settings):
    """A watched repository. Every key but ``repository`` overrides the same key of ``defaults``."""

    repository: RepositoryName
    mode: Literal["all", "labeled"] | None = None
    base_branch: str | None = None
    merge_method: Literal["squash", "merge", "rebase"] | None = None
    ci_repair_attempts: int | None = Field(default=None, ge=0)
    process_backlog: bool | None = None
    create_missing_labels: bool | None = None
    max_active_issues_per_repository: int | None = Field(default=None, ge=1)
    run_timeout: Duration | None = None
    checks_timeout: Duration | None = None
    build: str | None = None
    test: str | None = None
    forbidden_words: list[str] | None = None
    max_prose_lines: int | None = Field(default=None, ge=0)
    split_issues: bool | None = None


class Configuration(_Settings):
    """The whole configuration of one manager."""

    manager: ManagerSettings = Field(default_factory=ManagerSettings)
    poll_interval: Duration = timedelta(minutes=1)
    trusted_users: list[str] = Field(default_factory=list)
    ui: UiSettings = Field(default_factory=UiSettings)
    model_colors: ModelColorsSettings = Field(default_factory=ModelColorsSettings)
    accounts: dict[str, AccountSettings] = Field(default_factory=dict)
    workers: list[WorkerSettings] = Field(default_factory=list)
    fleet: FleetSettings = Field(default_factory=FleetSettings)
    defaults: DefaultsSettings = Field(default_factory=DefaultsSettings)
    repositories: list[RepositorySettings] = Field(default_factory=list)


class Problem(BaseModel):
    """One configuration problem: the JSON path of the offending key and what is wrong with it."""

    model_config = ConfigDict(frozen=True)

    path: str
    message: str


class ConfigurationError(ValueError):
    """The configuration is invalid. ``problems`` lists every problem found, not only the first."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(f"{p.path}: {p.message}" for p in problems))


def _json_path(location: tuple[int | str, ...]) -> str:
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else part)
    return path or "$"


def _semantic_problems(config: Configuration) -> list[Problem]:
    problems: list[Problem] = []
    seen: set[str] = set()
    for index, worker in enumerate(config.workers):
        at = f"workers[{index}]"
        if worker.name in seen:
            problems.append(
                Problem(path=f"{at}.name", message=f"duplicate worker name {worker.name!r}")
            )
        seen.add(worker.name)
        if re.search(r"\.\d+$", worker.name):
            problems.append(
                Problem(
                    path=f"{at}.name",
                    message=f"{worker.name!r} ends like an instance name (.2, .3, ...); rename it",
                )
            )
        if worker.account not in config.accounts:
            problems.append(
                Problem(path=f"{at}.account", message=f"no account {worker.account!r} in accounts")
            )
        if worker.agent == "command" and not worker.command:
            problems.append(
                Problem(path=f"{at}.command", message="a command worker needs a command")
            )
        installation = worker.installation
        if isinstance(installation, ClaudeLocalInstallation) and worker.agent != "claude":
            problems.append(
                Problem(
                    path=f"{at}.installation.type", message="claudelocal serves claude workers only"
                )
            )
        if (
            isinstance(installation, ClaudeCodePortableInstallation)
            and worker.agent not in _PORTABLE_AGENTS
        ):
            problems.append(
                Problem(
                    path=f"{at}.installation.type",
                    message="claudecodeportable serves claude, codex and antigravity workers only",
                )
            )

    if not _is_loopback(config.ui.bind) and not config.ui.token:
        problems.append(Problem(path="ui.token", message="a non-loopback ui.bind requires a token"))

    return problems


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate(data: Any) -> Configuration:
    """Validate already-decoded JSON data, raising `ConfigurationError` with every problem."""
    try:
        config = Configuration.model_validate(data)
    except ValidationError as error:
        problems = [
            Problem(path=_json_path(tuple(detail["loc"])), message=detail["msg"])
            for detail in error.errors()
        ]
        raise ConfigurationError(problems) from None

    problems = _semantic_problems(config)
    if problems:
        raise ConfigurationError(problems)
    return config


def parse(text: str) -> Configuration:
    """Parse and validate configuration JSON."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigurationError([Problem(path="$", message=f"invalid JSON: {error}")]) from None
    return validate(data)


def load(path: Path) -> Configuration:
    """Load the configuration at ``path``. A missing file is the all-defaults configuration."""
    if not path.exists():
        return Configuration()
    return parse(path.read_text(encoding="utf-8"))


def dump(config: Configuration) -> str:
    """Serialise ``config`` with only the explicitly set keys, as the file would hold it."""
    data = config.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save(config: Configuration, path: Path) -> None:
    """Write ``config`` to ``path`` atomically: readers see the old file or the new, never half."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(dump(config))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def default_path(platform: str = sys.platform, home: Path | None = None) -> Path:
    """The platform's configuration file: ``$XDG_CONFIG_HOME`` or ``~/.config`` on Linux,
    ``%APPDATA%`` on Windows. ``home`` replaces ``HOME``, so a launcher that relocated it does not
    give the manager a second configuration."""
    if platform == "win32":
        base = home / "AppData" / "Roaming" if home else Path(os.environ["APPDATA"])
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg and not (home and not Path(xdg).is_relative_to(home)):
            base = Path(xdg)
        else:
            base = (home or Path(os.environ["HOME"])) / ".config"
    return base / "GitHubIssueWorker" / "config.json"
