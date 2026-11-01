from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_issue_worker.adapters.agents.antigravity import AntigravityAdapter
from github_issue_worker.adapters.agents.claude import ClaudeAdapter
from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.ports.agents import AgentRun

FIXTURES = Path(__file__).parents[1] / "fixtures"


def lines(*events: dict[str, object]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_captured_quota_errors_are_not_response_activity(tmp_path: Path, kind: str) -> None:
    adapter = ClaudeAdapter("unused") if kind == "claude" else CodexAdapter()
    output = (FIXTURES / kind / "usage-limit.ndjson").read_text()
    run = AgentRun(tmp_path, "probe")
    assert not adapter.observe(run, output).worked
    assert adapter.report(run, 1, output, "").worked is False


def test_claude_success_after_a_quota_fallback_is_not_left_paused(tmp_path: Path) -> None:
    limited = (FIXTURES / "claude/usage-limit.ndjson").read_text()
    recovered = lines(
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": "Finished using the fallback"}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"outcome": "done", "summary": "Finished"},
        },
    )
    adapter = ClaudeAdapter("unused")
    run = AgentRun(tmp_path, "work")
    adapter.invocation(run)
    report = adapter.report(run, 0, limited + recovered, "")
    assert report.limit is None and report.error is None
    assert report.model == "claude-sonnet-5-5"
    assert report.outcome is not None
    assert adapter.report(run, 1, limited + recovered + limited, "").limit is not None


def test_codex_success_after_a_quota_fallback_is_not_left_paused(tmp_path: Path) -> None:
    limited = (FIXTURES / "codex/usage-limit.ndjson").read_text()
    recovered = lines(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Finished"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    )
    adapter = CodexAdapter()
    run = AgentRun(tmp_path, "work")
    adapter.invocation(run)
    (tmp_path / ".ghiw/outcome.json").write_text('{"outcome":"done","summary":"Finished"}')
    report = adapter.report(run, 0, limited + recovered, "")
    assert report.limit is None and report.error is None
    assert report.outcome is not None
    assert adapter.report(run, 1, limited + recovered + limited, "").limit is not None


def test_claude_tracks_response_models_after_the_initial_selection(tmp_path: Path) -> None:
    # Same event shapes as the captured Claude stream, with a switch between responses.
    output = lines(
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "Working"}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": "Continuing"}],
            },
        },
    )
    adapter = ClaudeAdapter("unused")
    run = AgentRun(tmp_path, "continue")
    observed = adapter.observe(run, output)
    assert observed.models == ("claude-opus-5-5", "claude-sonnet-5-5")
    assert observed.worked
    report = adapter.report(run, 1, output, "")
    assert report.model == "claude-sonnet-5-5"
    assert report.models == observed.models


def test_codex_reads_the_latest_turn_model_in_its_own_session(tmp_path: Path) -> None:
    session = "test-session"
    folder = tmp_path / "sessions"
    folder.mkdir()
    path = folder / f"rollout-{session}.jsonl"
    path.write_text(
        lines(
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            {"type": "turn_context", "payload": {"model": "gpt-6.1-terra"}},
            # A tool argument is not a model-selection event.
            {"type": "response_item", "payload": {"model": "not-the-model"}},
        ),
        encoding="utf-8",
    )
    adapter = CodexAdapter(env={"CODEX_HOME": str(tmp_path)})
    run = AgentRun(tmp_path, "continue", session=session)
    observed = adapter.observe(run, "")
    assert observed.models == ("gpt-6.1-terra",)
    assert not observed.worked
    assert (
        adapter.report(run, 1, lines({"type": "thread.started", "thread_id": session}), "").model
        == "gpt-6.1-terra"
    )


def test_antigravity_uses_its_conversation_log_and_detects_changes(tmp_path: Path) -> None:
    logs = tmp_path / ".gemini/antigravity-cli/log"
    logs.mkdir(parents=True)
    own = logs / "cli-20261030_100000.log"
    own.write_text(
        'Propagating selected model override to backend: label="Gemini 4 Argon (Medium)"\n'
        "Print mode: resuming conversation our-conversation\n",
        encoding="utf-8",
    )
    (logs / "cli-20261030_100001.log").write_text(
        'Propagating selected model override to backend: label="Other worker model"\n'
        "Print mode: resuming conversation another-conversation\n",
        encoding="utf-8",
    )
    adapter = AntigravityAdapter("unused", env={"HOME": str(tmp_path)})
    run = AgentRun(tmp_path, "continue", session="our-conversation")
    assert adapter.observe(run, "").models == ("Gemini 4 Argon (Medium)",)
    with own.open("a", encoding="utf-8") as stream:
        stream.write('Propagating selected model override to backend: label="Gemini fallback"\n')
    assert adapter.observe(run, "").models == ("Gemini fallback",)
    assert not adapter.observe(AgentRun(tmp_path, "new", session="unknown"), "").models


def test_antigravity_quota_only_continuation_is_not_work_despite_cumulative_usage(
    tmp_path: Path,
) -> None:
    output = (FIXTURES / "antigravity/usage-limit.ndjson").read_text()
    adapter = AntigravityAdapter("unused", env={"HOME": str(tmp_path)})
    run = AgentRun(tmp_path, "continue", session="7bf231d4-7811-4eb7-978d-a1c1ff0b3286")
    assert not adapter.observe(run, output).worked
    report = adapter.report(run, 1, output, "")
    assert report.limit is not None and report.worked is False
    assert report.usage is None  # the result's 487k tokens belong to the whole conversation


def test_real_work_before_a_limit_still_counts(tmp_path: Path) -> None:
    quota = (FIXTURES / "antigravity/usage-limit.ndjson").read_text()
    work = lines(
        {
            "event": "step_update",
            "step_update": {
                "step_index": 10,
                "step_type": "agent_response",
                "state": "DONE",
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        }
    )
    report = AntigravityAdapter("unused", env={"HOME": str(tmp_path)}).report(
        AgentRun(tmp_path, "continue", session="existing"), 1, work + quota, ""
    )
    assert report.limit is not None and report.worked is True
    assert report.usage is not None
    assert (report.usage.input_tokens, report.usage.output_tokens) == (12, 3)
