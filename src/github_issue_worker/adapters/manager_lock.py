"""Machine-local manager ownership, independent of configuration replacement (F3.6).

OS locks, rather than PID liveness checks, release ownership on process death. A short publication
lock serializes acquisition and PID writes so a concurrent loser cannot read a partial/stale PID.
Neither file is unlinked: an already-open contender must always lock the same inode.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO


class ManagerLockError(Exception):
    """Ownership could not be established safely."""


class AlreadyRunning(ManagerLockError):
    """A manager already owns this configuration."""

    def __init__(self, configuration: Path, pid: int) -> None:
        self.pid = pid
        super().__init__(f"{configuration}: manager already running with PID {pid}")


def _try_lock(stream: BinaryIO) -> bool:
    stream.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            # Lock only the sentinel byte. Other handles must still be able to read the PID.
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise
    return True


@contextmanager
def manager_lock(configuration: Path) -> Iterator[Path]:
    """Acquire exclusive ownership or report the existing PID; fail closed on I/O errors."""
    with ExitStack() as files:
        try:
            target = configuration.resolve()
            canonical = os.path.normcase(str(target))
            key = hashlib.sha256(os.fsencode(canonical)).hexdigest()
            directory = Path(tempfile.gettempdir()) / "GitHubIssueWorker-locks"
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            publication = files.enter_context((directory / f"{key}.guard").open("a+b"))
            deadline = time.monotonic() + 2
            while not _try_lock(publication):
                if time.monotonic() >= deadline:
                    raise ManagerLockError(f"{configuration}: timed out publishing manager lock")
                time.sleep(0.01)
            owner = files.enter_context((directory / f"{key}.lock").open("a+b"))
            if not _try_lock(owner):
                owner.seek(1)
                pid = int(owner.read().decode("ascii").strip())
                if pid <= 0:
                    raise ValueError("invalid owner PID")
                raise AlreadyRunning(configuration, pid)
            # Do not use append writes: the PID must replace stale metadata. Byte zero is the
            # Windows lock sentinel; keeping metadata after it allows reads by contenders.
            owner.seek(0)
            owner.truncate(0)
            owner.write(b"\0" + str(os.getpid()).encode("ascii") + b"\n")
            owner.flush()
            publication.close()
        except (OSError, ValueError) as error:
            raise ManagerLockError(
                f"{configuration}: cannot acquire manager lock: {error}"
            ) from error
        yield target
