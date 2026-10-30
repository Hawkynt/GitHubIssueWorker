from __future__ import annotations

import json
from pathlib import Path

from github_issue_worker.adapters.transcript import transcript

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_claude_turns_into_messages_tool_calls_and_results() -> None:
    entries = transcript(read("claude/first-run.ndjson"))
    kinds = [entry.kind for entry in entries]
    assert "tool" in kinds and "output" in kinds and "done" in kinds
    tool = next(entry for entry in entries if entry.kind == "tool")
    assert tool.title  # the tool's name
    assert entries[-1].kind == "done"


def test_codex_turns_into_messages_commands_and_their_output() -> None:
    entries = transcript(read("codex/first-run.ndjson"))
    messages = [entry.text for entry in entries if entry.kind == "message"]
    assert any("hello.txt" in text for text in messages)
    command = next(entry for entry in entries if entry.kind == "tool")
    assert "bash" in command.text
    assert any(entry.kind == "output" for entry in entries)


def test_antigravity_turns_into_tool_steps_and_its_answer() -> None:
    entries = transcript(read("antigravity/first-run.ndjson"))
    assert [entry.title for entry in entries if entry.kind == "tool"] == [
        "read_file",
        "read_file",
        "write_file",
    ]
    assert entries[-1].kind == "done" and "done" in entries[-1].text


def test_errors_are_called_out() -> None:
    line = json.dumps({"type": "error", "message": "You've hit your usage limit."})
    (entry,) = transcript(line)
    assert entry.kind == "error" and "usage limit" in entry.text


def test_anything_unrecognised_is_shown_as_it_is() -> None:
    entries = transcript("plain text from a launcher\n" + json.dumps({"odd": 1}))
    assert [entry.kind for entry in entries] == ["raw", "raw"]
    assert entries[0].text == "plain text from a launcher"
