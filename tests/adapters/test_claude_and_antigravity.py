from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from github_issue_worker.adapters.agents.antigravity import AntigravityAdapter
from github_issue_worker.adapters.agents.claude import ClaudeAdapter, ClaudeLocalAdapter
from github_issue_worker.ports.agents import PROMPT_FILE, AgentRun

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run(tmp_path: Path, session: str | None = None) -> AgentRun:
    return AgentRun(worktree=tmp_path, prompt="Fix issue 42.", session=session, model="opus")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- Claude Code ---------------------------------------------------------------------------------


def test_claude_runs_headless_without_prompts_and_with_a_known_session(tmp_path: Path) -> None:
    invocation = ClaudeAdapter("claude", env={"CLAUDE_CONFIG_DIR": "/p"}).invocation(run(tmp_path))
    argv = invocation.argv
    assert argv[:2] == ["claude", "-p"]
    assert {"--verbose", "--dangerously-skip-permissions"} <= set(argv)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--session-id" in argv  # chosen up front, so a crash still leaves a resumable id
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["includeCoAuthoredBy"] is False
    assert {"Bash(git push:*)", "Bash(gh:*)"} <= set(settings["permissions"]["deny"])
    assert json.loads(argv[argv.index("--json-schema") + 1])["required"]
    assert invocation.stdin == "Fix issue 42."
    assert invocation.env == {"CLAUDE_CONFIG_DIR": "/p"}
    assert (tmp_path / PROMPT_FILE).read_text(encoding="utf-8") == "Fix issue 42."


def test_claude_resumes_a_session(tmp_path: Path) -> None:
    argv = ClaudeAdapter("claude").invocation(run(tmp_path, session="abc")).argv
    assert argv[argv.index("--resume") + 1] == "abc"
    assert "--session-id" not in argv


def test_claude_structured_output_becomes_the_outcome(tmp_path: Path) -> None:
    adapter = ClaudeAdapter("claude")
    adapter.invocation(run(tmp_path))
    report = adapter.report(run(tmp_path), 0, fixture("claude/first-run.ndjson"), "")
    assert report.error is None
    assert report.session == "1d8cbbe2-b4ca-4003-b49f-7ea099f2c425"
    assert report.outcome is not None and report.outcome.kind == "done"
    assert report.usage is not None and report.usage.cost_usd is not None
    assert report.utilization == 0.37  # the captured run carried an allowed_warning at 37 %
    assert report.limit is None


def test_claude_usage_limit_carries_the_reset(tmp_path: Path) -> None:
    report = ClaudeAdapter("claude").report(
        run(tmp_path), 1, fixture("claude/usage-limit.ndjson"), ""
    )
    assert report.limit is not None
    assert report.limit.reset_at == datetime.fromtimestamp(1793948400, tz=UTC)


def test_claude_exiting_with_no_output_is_an_error(tmp_path: Path) -> None:
    report = ClaudeAdapter("claude").report(run(tmp_path), 1, "", "No messages returned")
    assert report.outcome is None and report.limit is None
    assert report.error is not None and "No messages returned" in report.error


def test_claudelocal_goes_through_its_launcher(tmp_path: Path) -> None:
    adapter = ClaudeLocalAdapter(Path("/opt/ClaudeLocal/claude.sh"))
    argv = adapter.invocation(run(tmp_path)).argv
    assert argv[:5] == ["/opt/ClaudeLocal/claude.sh", "--dir", str(tmp_path), "--prompt", "-"]
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "opus"
    assert "--stop" not in argv and "--tune" not in argv


def test_claudelocal_reads_the_outcome_file_and_the_json_result(tmp_path: Path) -> None:
    adapter = ClaudeLocalAdapter(Path("claude.sh"))
    adapter.invocation(run(tmp_path))
    (tmp_path / ".ghiw" / "outcome.json").write_text(
        '{"outcome": "done", "summary": "ok"}', encoding="utf-8"
    )
    stdout = json.dumps({"type": "result", "is_error": False, "session_id": "s1", "result": "ok"})
    report = adapter.report(run(tmp_path), 0, stdout, "")
    assert report.session == "s1" and report.outcome is not None and report.error is None


# --- Antigravity ---------------------------------------------------------------------------------


def test_antigravity_gets_a_pointer_to_the_prompt_file(tmp_path: Path) -> None:
    adapter = AntigravityAdapter("agy", env={"HOME": "/profile"})
    invocation = adapter.invocation(run(tmp_path, session="conv-1"))
    argv = invocation.argv
    assert argv[:2] == ["agy", "-p"]
    assert ".ghiw/prompt.md" in argv[2]
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--conversation") + 1] == "conv-1"
    assert invocation.stdin == ""
    assert (tmp_path / PROMPT_FILE).read_text(encoding="utf-8") == "Fix issue 42."


def test_antigravity_captured_run_reports_its_conversation(tmp_path: Path) -> None:
    adapter = AntigravityAdapter("agy")
    adapter.invocation(run(tmp_path))
    (tmp_path / ".ghiw" / "outcome.json").write_text(
        '{"outcome": "done", "summary": "ok"}', encoding="utf-8"
    )
    report = adapter.report(run(tmp_path), 0, fixture("antigravity/first-run.ndjson"), "")
    assert report.session == "5e8a6cfe-a43d-442e-a71f-b2ebc5cd5cce"
    assert report.error is None and report.outcome is not None
    assert report.usage is not None and report.usage.output_tokens == 409


def test_antigravity_failure_status_is_an_error(tmp_path: Path) -> None:
    stdout = json.dumps({"event": "result", "result": {"status": "ERROR", "response": "boom"}})
    report = AntigravityAdapter("agy").report(run(tmp_path), 1, stdout, "")
    assert report.error is not None and "boom" in report.error


def test_antigravity_quota_messages_are_usage_limits(tmp_path: Path) -> None:
    stdout = json.dumps(
        {"event": "result", "result": {"status": "ERROR", "response": "Quota exceeded (429)."}}
    )
    report = AntigravityAdapter("agy").report(run(tmp_path), 1, stdout, "")
    assert report.limit is not None and report.limit.reset_at is None
