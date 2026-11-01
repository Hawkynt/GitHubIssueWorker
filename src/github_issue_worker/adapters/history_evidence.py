"""Recover work evidence for old history from each invocation's retained transcript (F14.13)."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from github_issue_worker.adapters.agents.antigravity import _LIMIT as AGY_LIMIT
from github_issue_worker.adapters.agents.antigravity import AntigravityAdapter
from github_issue_worker.adapters.agents.claude import ClaudeAdapter, _events
from github_issue_worker.adapters.agents.codex import _LIMIT as CODEX_LIMIT
from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.application.history import RunRecord
from github_issue_worker.ports.agents import AgentRun, ObservableAgent

_NAME = re.compile(r"^(\d{8}T\d{6})-(.+?)(?:-[0-9a-f]{32})?\.ndjson$")


class HistoryEvidence:
    def __init__(self, logs: Path, manager: str) -> None:
        self._logs = logs
        self._manager = manager
        # Replay only stdout. Never inspect current vendor sessions or launch a vendor CLI.
        home = str(logs / ".history-replay")
        self._observers: dict[str, ObservableAgent] = {
            "antigravity": AntigravityAdapter("", env={"HOME": home}),
            "codex": CodexAdapter(env={"CODEX_HOME": home}),
            "claude": ClaudeAdapter(""),
            "local": ClaudeAdapter(""),
        }
        self._cache: dict[Path, tuple[int, int, bool | None]] = {}

    def restore(self, records: list[RunRecord]) -> list[RunRecord]:
        indexed: dict[tuple[str, int], list[tuple[datetime, str, Path]]] = {}
        restored = []
        for record in records:
            if (
                record.active is not None
                or record.verdict != "paused"
                or record.manager not in ("", self._manager)
            ):
                restored.append(record)
                continue
            key = (record.repository, record.issue)
            if key not in indexed:
                files = []
                for path in (self._logs / record.repository / str(record.issue)).glob("*.ndjson"):
                    match = _NAME.fullmatch(path.name)
                    if match:
                        try:
                            stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                        except ValueError:
                            continue
                        files.append((stamp, match[2], path))
                indexed[key] = files
            start = record.started.replace(microsecond=0)
            end = record.finished.replace(microsecond=999999)
            evidence = [
                self._activity(path, record.agent)
                for stamp, worker, path in indexed[key]
                if worker == record.worker and start <= stamp <= end
            ]
            active = (
                True
                if True in evidence
                else False
                if evidence and all(e is False for e in evidence)
                else None
            )
            restored.append(replace(record, active=active))
        return restored

    def _activity(self, path: Path, agent: str) -> bool | None:
        observer = self._observers.get(agent)
        if observer is None:
            return None
        try:
            stat = path.stat()
            cached = self._cache.get(path)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        active: bool | None = None
        if observer.observe(AgentRun(self._logs, ""), text).worked:
            active = True
        else:
            events = _events(text)
            if events:
                last = events[-1]
                if agent == "antigravity" and last.get("event") == "result":
                    result = last.get("result") or {}
                    if result.get("status") == "ERROR" and AGY_LIMIT.search(
                        str(result.get("error") or result.get("response") or "")
                    ):
                        active = False
                elif agent == "codex" and last.get("type") == "turn.failed":
                    if CODEX_LIMIT.search(str((last.get("error") or {}).get("message", ""))):
                        active = False
                elif (
                    agent in ("claude", "local")
                    and last.get("type") == "result"
                    and last.get("is_error")
                ):
                    if any(
                        e.get("api_error") == "usage_limit_reached"
                        or (
                            e.get("type") == "rate_limit_event"
                            and (e.get("rate_limit_info") or {}).get("status") == "rejected"
                        )
                        for e in events
                    ):
                        active = False
        self._cache[path] = (stat.st_mtime_ns, stat.st_size, active)
        return active
