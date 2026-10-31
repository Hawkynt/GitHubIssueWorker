"""Authenticated local removal requests, independent of the web UI (F1.4).

The standard library supplies HTTP parsing and transport. A process-scoped file lease serializes
manager startup and offline removal; it never reserves an issue or replaces a repository claim.
Only an acknowledged request is success. A busy lease with an unreachable server is an error,
never permission to checkpoint a worktree another process may still be changing.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import sys
import tempfile
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


class ControlError(Exception):
    """Removal was not acknowledged; it is safe to retry, not to assume completion."""


class ControlLease:
    """One manager or offline removal operation per state directory, released on process exit."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._file: BinaryIO | None = None

    def acquire(self) -> bool:
        self.directory.mkdir(parents=True, exist_ok=True)
        stream = (self.directory / "control.lock").open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self._file = stream
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> ControlLease:
        if not self.acquire():
            raise ControlError("another manager or removal operation is using this state directory")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class ControlServer:
    def __init__(self, directory: Path, config: Path, remove: Callable[[str], None]) -> None:
        self._descriptor = directory / "control.json"
        self._config = str(config.resolve())
        self._remove = remove
        self._token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> ControlServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                if self.path != "/remove":
                    self.send_error(404)
                    return
                if not secrets.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {owner._token}"
                ):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 8192:
                        raise ValueError("invalid request size")
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError("invalid request object")
                    if data.get("config") != owner._config:
                        raise ValueError("configuration does not match the running manager")
                    repo = data.get("repository")
                    if not isinstance(repo, str) or not repo:
                        raise ValueError("missing repository")
                    owner._remove(repo)
                except Exception as error:
                    self._reply(409, str(error))
                    return
                self._reply(200, "removed")

            def _reply(self, status: int, text: str) -> None:
                payload = json.dumps({"message": text}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(payload)

        # Non-daemon request threads are joined by server_close before the lease is released.
        # A disconnected client must never make an in-flight removal look like offline work.
        class Server(ThreadingHTTPServer):
            daemon_threads = False

        server = self._server = Server(("127.0.0.1", 0), Handler)
        self._descriptor.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".control-", dir=self._descriptor.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"port": server.server_port, "token": self._token, "config": self._config},
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._descriptor)
            self._thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
            )
            self._thread.start()
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            server.server_close()
            self._descriptor.unlink(missing_ok=True)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()
        self._descriptor.unlink(missing_ok=True)


def remove_or_request(
    directory: Path, config: Path, repository: str, offline: Callable[[], None]
) -> None:
    lease = ControlLease(directory)
    if lease.acquire():
        try:
            offline()
        finally:
            lease.close()
        return
    try:
        descriptor = json.loads((directory / "control.json").read_text(encoding="utf-8"))
        if not isinstance(descriptor, dict):
            raise ValueError("invalid manager descriptor")
        if descriptor.get("config") != str(config.resolve()):
            raise ControlError("configuration does not match the running manager")
        port = int(descriptor["port"])
        if not 0 < port < 65536:
            raise ValueError("invalid control port")
        payload = json.dumps({"config": str(config.resolve()), "repository": repository}).encode()
        request = Request(
            f"http://127.0.0.1:{port}/remove",
            payload,
            {"Authorization": f"Bearer {descriptor['token']}", "Content-Type": "application/json"},
        )
        # Local control never uses a configured HTTP proxy.
        with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
            if response.status != 200:
                raise ControlError("removal was not acknowledged; retry")
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise ControlError(f"removal refused ({error.code}): {detail}") from error
    except (OSError, ValueError, KeyError, URLError) as error:
        raise ControlError(f"manager control unavailable; retry removal: {error}") from error
