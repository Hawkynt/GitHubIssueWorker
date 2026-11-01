"""Each configuration keeps its own local state and cache by default (F3.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from github_issue_worker.cli import main
from github_issue_worker.configuration import Configuration, default_path, save
from github_issue_worker.wiring import build_runtime, cache_dir, state_dir
from tests.test_cli import Runtime

Dirs = tuple[Path | None, Path | None]


def _directories(argv: list[str], home: Path) -> Dirs:
    seen: list[Dirs] = []

    def factory(config: Configuration, at: Path, state: Path | None, cache: Path | None) -> Runtime:
        seen.append((state, cache))
        raise ValueError("stop after wiring")

    main([*argv, "status"], home=home, runtime_factory=factory)
    return seen[0]


def test_given_the_default_configuration_when_the_manager_starts_then_it_keeps_the_legacy_dirs(
    tmp_path: Path,
) -> None:
    save(Configuration(), default_path(home=tmp_path))
    explicit = ["--config", str(default_path(home=tmp_path))]
    assert _directories([], tmp_path) == (None, None)
    assert _directories(explicit, tmp_path) == (None, None)


def test_given_two_configurations_when_both_start_then_state_and_cache_are_separate(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "a" / "config.json", tmp_path / "b" / "config.json"
    save(Configuration(), first)
    save(Configuration(), second)
    one = _directories(["--config", str(first)], tmp_path)
    two = _directories(["--config", str(second)], tmp_path)
    assert None not in one and None not in two
    assert one[0] != two[0] and one[1] != two[1]
    for state, cache in (one, two):
        assert state is not None and cache is not None
        assert state.is_relative_to(state_dir(tmp_path)) and state.name == "state.json"
        assert state.parent != state_dir(tmp_path)
        assert cache.is_relative_to(cache_dir(tmp_path)) and cache != cache_dir(tmp_path)


def test_given_a_relative_alias_when_the_manager_starts_then_it_reuses_the_same_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    save(Configuration(), path)
    monkeypatch.chdir(tmp_path)
    assert _directories(["--config", "config.json"], tmp_path) == _directories(
        ["--config", str(path)], tmp_path
    )


def test_given_explicit_dirs_when_the_manager_starts_then_they_override_the_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    save(Configuration(), path)
    argv = ["--config", str(path), "--state-dir", str(tmp_path / "s")]
    argv += ["--cache-dir", str(tmp_path / "c")]
    assert _directories(argv, tmp_path) == (tmp_path / "s" / "state.json", tmp_path / "c")


def test_given_two_configurations_with_one_manager_name_then_their_manager_ids_differ(
    tmp_path: Path,
) -> None:
    """Regression: a shared default state gave both managers one id, so both adopted one claim."""
    ids: list[str] = []

    def factory(config: Configuration, at: Path, state: Path | None, cache: Path | None) -> Runtime:
        ids.append(build_runtime(config, at, state, cache).manager_id)
        raise ValueError("stop after wiring")

    for name in ("a", "b"):
        path = tmp_path / name / "config.json"
        save(Configuration(), path)
        main(["--config", str(path), "status"], home=tmp_path, runtime_factory=factory)
    assert len(ids) == 2 and ids[0] != ids[1]
