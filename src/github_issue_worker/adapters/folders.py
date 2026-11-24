"""The folders the add-worker page's folder browser shows (F14.4).

A browser cannot hand a page the full path of a folder the user picked, so the manager lists folders
itself: only folders, never files, starting at the account home. On Windows the drives are one
level above every drive's top. The page is loopback-only by default and token-protected otherwise,
like every other page of the UI.
"""

from __future__ import annotations

import os
import string
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FolderListing:
    path: str
    """The folder listed; empty for the list of drives."""
    folders: tuple[str, ...]
    """Full paths of its subfolders, sorted by name, case-insensitively."""
    parent: str | None
    """The folder above; empty means the list of drives, None means there is nothing above."""
    portable: bool = False
    """It looks like a ClaudeCodePortable installation: it has a ``profiles`` folder."""
    problem: str = ""


def list_folders(path: str, home: Path, drives: bool = False) -> FolderListing:
    """The subfolders of ``path`` (``~`` is the account home; empty is the home itself), or the
    drives when ``drives`` is set on Windows."""
    if drives and sys.platform == "win32":
        found = tuple(
            f"{letter}:\\" for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:\\")
        )
        return FolderListing("", found, None)
    if not path.strip():
        folder = home
    elif path == "~" or path.startswith(("~/", "~\\")):
        folder = home / path[2:]
    else:
        folder = Path(path)
    folder = Path(os.path.abspath(folder))
    above = folder.parent
    parent: str | None = (
        str(above) if above != folder else ("" if sys.platform == "win32" else None)
    )
    if not folder.is_dir():
        return FolderListing(str(folder), (), parent, problem=f"{folder} is not a folder")
    try:
        names = sorted(
            (entry.name for entry in os.scandir(folder) if entry.is_dir(follow_symlinks=True)),
            key=str.casefold,
        )
    except OSError as error:
        return FolderListing(str(folder), (), parent, problem=f"{folder}: {error.strerror}")
    return FolderListing(
        str(folder),
        tuple(str(folder / name) for name in names),
        parent,
        portable=(folder / "profiles").is_dir(),
    )
