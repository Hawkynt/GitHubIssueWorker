"""The home directory gh and git run with, whatever HOME the manager was started under (F0.5).

Launchers such as ClaudeCodePortable and ClaudeLocal relocate ``HOME`` (and ``USERPROFILE`` on
Windows) into their own profile directories. ``gh`` then no longer finds its login: measured here,
``gh auth status`` says "not logged into any GitHub hosts" under the relocated home and is logged in
with the real one. The manager therefore runs ``gh`` and every networked ``git`` call with the
account's home from the operating system's user database, or the configured ``manager.home``.
Agents are deliberately not given it: they must not reach GitHub at all.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def _passwd_home() -> str:
    import pwd

    return pwd.getpwuid(os.getuid()).pw_dir


def _windows_profile() -> str:
    """The profile folder from the shell, which reads the registry rather than the environment."""
    import ctypes
    import uuid
    from ctypes import wintypes

    folder_profile = uuid.UUID("{5E6C858F-0E22-4760-9AFE-EA3317B67173}")
    guid = ctypes.create_string_buffer(folder_profile.bytes_le)
    path = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    if shell32.SHGetKnownFolderPath(guid, 0, wintypes.HANDLE(0), ctypes.byref(path)) != 0:
        raise OSError("SHGetKnownFolderPath(FOLDERID_Profile) failed")
    try:
        return str(path.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(path)  # type: ignore[attr-defined]


def account_home(configured: str | None = None, platform: str = sys.platform) -> Path:
    """``manager.home`` when set, else the account's home as the operating system records it."""
    if configured:
        return Path(configured).expanduser()
    return Path(_windows_profile() if platform == "win32" else _passwd_home())


def tool_environment(
    home: Path, base: Mapping[str, str] | None = None, platform: str = sys.platform
) -> dict[str, str]:
    """The environment for ``gh`` and networked ``git``: ``base`` with the home put back.

    A config home inside the relocated home belongs to the relocation and is dropped; one set
    elsewhere, and an explicit ``GH_CONFIG_DIR``, are deliberate and kept.
    """
    env = dict(os.environ if base is None else base)
    relocated = env.get("USERPROFILE" if platform == "win32" else "HOME")
    config_home = env.get("XDG_CONFIG_HOME")
    if relocated and config_home and Path(config_home).is_relative_to(relocated):
        del env["XDG_CONFIG_HOME"]

    env["HOME"] = str(home)
    if platform == "win32":
        env["USERPROFILE"] = str(home)
    return env
