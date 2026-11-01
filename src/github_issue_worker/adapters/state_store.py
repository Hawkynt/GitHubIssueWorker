"""The manager's local state (F3.3): poll cursors, conversation ids, account limits, its id.

Everything here is a cache of what the repositories also know or can re-learn; losing it costs a
re-poll and fresh conversations, never work. Writes are atomic (temporary file, fsync, rename).
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from collections.abc import Iterator, MutableMapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_issue_worker.application.poller import PollState


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def _stamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


class _Sessions(MutableMapping[tuple[int, str], str]):
    def __init__(self, store: JsonStateStore, repository: str) -> None:
        self._store = store
        self._repository = repository

    def _data(self) -> dict[str, str]:
        sessions: dict[str, str] = self._store._data.setdefault("sessions", {}).setdefault(
            self._repository, {}
        )
        return sessions

    @staticmethod
    def _key(key: tuple[int, str]) -> str:
        return f"{key[0]}#{key[1]}"

    def __getitem__(self, key: tuple[int, str]) -> str:
        return self._data()[self._key(key)]

    def __setitem__(self, key: tuple[int, str], value: str) -> None:
        with self._store._lock:
            self._data()[self._key(key)] = value
            self._store._save()

    def __delitem__(self, key: tuple[int, str]) -> None:
        with self._store._lock:
            del self._data()[self._key(key)]
            self._store._save()

    def __iter__(self) -> Iterator[tuple[int, str]]:
        for key in list(self._data()):
            number, _, worker = key.partition("#")
            yield int(number), worker

    def __len__(self) -> int:
        return len(self._data())


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def manager_id(self, name: str) -> str:
        """``<name>-<random>``, chosen once and kept, so claims survive restarts."""
        with self._lock:
            if "managerId" not in self._data:
                self._data["managerId"] = f"{name}-{secrets.token_hex(2)}"
                self._save()
            return str(self._data["managerId"])

    def poll_state(self, repository: str) -> PollState:
        raw = self._data.get("poll", {}).get(repository)
        if not raw:
            return PollState()
        return PollState(
            watched_since=_time(raw.get("watched_since")),
            issues_since=_time(raw.get("issues_since")),
            comments_since=_time(raw.get("comments_since")),
            issues_etag=raw.get("issues_etag"),
            comments_etag=raw.get("comments_etag"),
            last_polled_at=_time(raw.get("last_polled_at")),
        )

    def set_poll_state(self, repository: str, state: PollState) -> None:
        raw = {
            key: (_stamp(value) if isinstance(value, datetime) else value)
            for key, value in asdict(state).items()
        }
        with self._lock:
            self._data.setdefault("poll", {})[repository] = raw
            self._save()

    def sessions(self, repository: str) -> MutableMapping[tuple[int, str], str]:
        return _Sessions(self, repository)

    def limited_until(self, account: str) -> datetime | None:
        return _time(self._data.get("accounts", {}).get(account))

    def set_limited(self, account: str, until: datetime | None) -> None:
        with self._lock:
            self._data.setdefault("accounts", {})[account] = _stamp(until)
            self._save()

    def model(self, worker: str) -> str | None:
        """The model the worker's last run reported."""
        value = self._data.get("models", {}).get(worker)
        return str(value) if value else None

    def set_model(self, worker: str, model: str) -> None:
        with self._lock:
            self._data.setdefault("models", {})[worker] = model
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=".state.", dir=self._path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self._data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
