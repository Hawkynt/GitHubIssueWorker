"""F14.4 — the folder browser of the add-worker page lists folders, never files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from github_issue_worker.adapters.folders import list_folders


def test_given_a_folder_when_listed_then_its_subfolders_come_sorted_without_files(
    tmp_path: Path,
) -> None:
    for name in ("b", "A", "c"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")

    listing = list_folders(str(tmp_path), tmp_path)

    assert listing.path == str(tmp_path)
    assert [Path(folder).name for folder in listing.folders] == ["A", "b", "c"]
    assert listing.parent == str(tmp_path.parent)
    assert listing.problem == ""


def test_given_a_portable_installation_when_listed_then_it_is_recognised(tmp_path: Path) -> None:
    root = tmp_path / "ClaudePortable"
    (root / "profiles" / "me").mkdir(parents=True)
    assert list_folders(str(root), tmp_path).portable is True
    assert list_folders(str(tmp_path), tmp_path).portable is False


def test_given_no_folder_when_listed_then_the_account_home_is_shown(tmp_path: Path) -> None:
    assert list_folders("", tmp_path).path == str(tmp_path)


def test_given_a_tilde_when_listed_then_it_means_the_account_home(tmp_path: Path) -> None:
    (tmp_path / "Agents").mkdir()
    assert list_folders("~/Agents", tmp_path).path == str(tmp_path / "Agents")


@pytest.mark.parametrize("name", ["missing", "file.txt"])
def test_given_something_that_is_no_folder_when_listed_then_it_says_so(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    listing = list_folders(str(tmp_path / name), tmp_path)
    assert listing.folders == ()
    assert "not a folder" in listing.problem


def test_given_the_top_of_a_drive_when_listed_then_its_parent_is_the_drive_list(
    tmp_path: Path,
) -> None:
    anchor = Path(tmp_path.anchor)
    listing = list_folders(str(anchor), tmp_path)
    assert listing.parent == ("" if sys.platform == "win32" else None)


@pytest.mark.skipif(sys.platform != "win32", reason="drives exist on Windows only")
def test_given_the_drive_list_when_listed_then_every_drive_is_a_folder(tmp_path: Path) -> None:
    listing = list_folders("", tmp_path, drives=True)
    assert Path(tmp_path.anchor).as_posix().upper() in {
        Path(f).as_posix().upper() for f in listing.folders
    }
    assert listing.parent is None
