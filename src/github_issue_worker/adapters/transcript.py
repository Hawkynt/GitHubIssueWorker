"""An agent run's output as a readable chat log: messages, tool calls, their output, errors.

Each agent streams its own event format (shapes from the captured runs in ``tests/fixtures``). The
log page reads the run's output file while it is still being written, so a partial last line is
normal and skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

EntryKind = Literal["message", "thinking", "tool", "output", "done", "error", "raw"]
_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class Entry:
    kind: EntryKind
    text: str
    title: str = ""


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= _LIMIT else text[:_LIMIT] + " ..."


def _claude(event: dict[str, Any]) -> list[Entry]:
    kind = event.get("type")
    if kind == "result":
        return [Entry("done", _short(event.get("result") or event.get("subtype") or ""))]
    if kind not in ("assistant", "user"):
        return []
    entries = []
    for part in (event.get("message") or {}).get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            entries.append(Entry("message", _short(part["text"])))
        elif part.get("type") == "thinking" and part.get("thinking"):
            entries.append(Entry("thinking", _short(part["thinking"])))
        elif part.get("type") == "tool_use":
            entries.append(Entry("tool", _short(part.get("input") or {}), part.get("name", "tool")))
        elif part.get("type") == "tool_result":
            content = part.get("content")
            if isinstance(content, list):
                content = "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
            entries.append(Entry("error" if part.get("is_error") else "output", _short(content)))
    return entries


def _codex(event: dict[str, Any]) -> list[Entry]:
    kind = event.get("type")
    if kind == "error":
        return [Entry("error", _short(event.get("message", "")))]
    if kind == "turn.failed":
        return [Entry("error", _short((event.get("error") or {}).get("message", "")))]
    if kind != "item.completed":
        return []
    item = event.get("item") or {}
    if item.get("type") == "agent_message":
        return [Entry("message", _short(item.get("text", "")))]
    if item.get("type") == "reasoning" and item.get("text"):
        return [Entry("thinking", _short(item["text"]))]
    if item.get("type") == "command_execution":
        entries = [Entry("tool", _short(item.get("command", "")), "command")]
        output = item.get("aggregated_output")
        if output:
            entries.append(Entry("output", _short(output)))
        return entries
    return []


def _antigravity(event: dict[str, Any], answer: list[str]) -> list[Entry]:
    kind = event.get("event")
    if kind == "result":
        result = event.get("result") or {}
        text = result.get("response") or "".join(answer) or result.get("status", "")
        failed = result.get("status") not in (None, "SUCCESS")
        return [Entry("error" if failed else "done", _short(text))]
    if kind != "step_update":
        return []
    step = event.get("step_update") or {}
    if step.get("step_type") == "agent_response" and step.get("text_delta"):
        answer.append(step["text_delta"])
        return []
    if step.get("step_type") == "tool" and step.get("state") == "DONE":
        info = step.get("tool_info") or {}
        entries = [
            Entry("tool", _short(info.get("parameters") or {}), step.get("tool_name", "tool"))
        ]
        if info.get("output"):
            entries.append(Entry("output", _short(info["output"])))
        return entries
    return []


def transcript(text: str) -> list[Entry]:
    entries: list[Entry] = []
    answer: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if not line.startswith("{"):  # a partial last line of a running agent is skipped
                entries.append(Entry("raw", _short(line)))
            continue
        if not isinstance(event, dict):
            entries.append(Entry("raw", _short(line)))
            continue
        if "event" in event:
            found = _antigravity(event, answer)
        elif str(event.get("type", "")).startswith(("item.", "thread.", "turn.")) or (
            event.get("type") == "error"
        ):
            found = _codex(event)
        elif event.get("type") in ("assistant", "user", "result", "system", "rate_limit_event"):
            found = _claude(event)
        else:
            found = [Entry("raw", _short(line))]
        entries += found
    return entries
