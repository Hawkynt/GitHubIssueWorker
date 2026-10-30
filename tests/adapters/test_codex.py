from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from github_issue_worker.adapters.agents.codex import CodexAdapter
from github_issue_worker.ports.agents import AgentRun

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "codex"
BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 30, 14, 0, tzinfo=BERLIN)


def adapter() -> CodexAdapter:
    return CodexAdapter(executable="codex", timezone=BERLIN, now=lambda: NOW)


def run(tmp_path: Path, session: str | None = None, model: str | None = None) -> AgentRun:
    return AgentRun(worktree=tmp_path, prompt="Fix issue 42.", session=session, model=model)


def test_a_first_run_is_headless_schema_bound_and_reads_the_prompt_from_stdin(
    tmp_path: Path,
) -> None:
    invocation = adapter().invocation(run(tmp_path, model="gpt-5"))
    argv = invocation.argv
    assert argv[:3] == ["codex", "exec", "--json"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("-o") + 1] == str(tmp_path / ".ghiw" / "outcome.json")
    schema = Path(argv[argv.index("--output-schema") + 1])
    assert json.loads(schema.read_text(encoding="utf-8"))["required"]
    assert argv[argv.index("-m") + 1] == "gpt-5"
    assert argv[-1] == "-"
    assert invocation.stdin == "Fix issue 42."
    assert invocation.cwd == tmp_path


def test_a_continuation_resumes_the_session(tmp_path: Path) -> None:
    argv = adapter().invocation(run(tmp_path, session="01a18d18")).argv
    assert argv[:4] == ["codex", "exec", "resume", "01a18d18"]
    assert "--json" in argv and argv[-1] == "-"


def test_a_captured_run_reports_session_usage_and_outcome(tmp_path: Path) -> None:
    (tmp_path / ".ghiw").mkdir()
    (tmp_path / ".ghiw" / "outcome.json").write_text(
        '{"outcome":"done","summary":"Created hello.txt containing exactly hi."}', encoding="utf-8"
    )
    stdout = (FIXTURES / "first-run.ndjson").read_text(encoding="utf-8")
    report = adapter().report(run(tmp_path), exit_code=0, stdout=stdout, stderr="")
    assert report.session == "01a18d18-826a-7971-9098-50818d41b1a6"
    assert report.outcome is not None and report.outcome.kind == "done"
    assert report.limit is None and report.error is None
    assert report.usage is not None and report.usage.output_tokens == 241


def test_a_resumed_run_keeps_the_same_session(tmp_path: Path) -> None:
    stdout = (FIXTURES / "resume.ndjson").read_text(encoding="utf-8")
    report = adapter().report(run(tmp_path, session="x"), exit_code=0, stdout=stdout, stderr="")
    assert report.session == "01a18d18-826a-7971-9098-50818d41b1a6"


def test_no_outcome_file_is_an_error_not_a_success(tmp_path: Path) -> None:
    stdout = (FIXTURES / "first-run.ndjson").read_text(encoding="utf-8")
    report = adapter().report(run(tmp_path), exit_code=0, stdout=stdout, stderr="")
    assert report.outcome is None
    assert report.error is not None and "outcome" in report.error


def test_a_usage_limit_is_detected_with_its_reset_time(tmp_path: Path) -> None:
    stdout = (FIXTURES / "usage-limit.ndjson").read_text(encoding="utf-8")
    report = adapter().report(run(tmp_path), exit_code=1, stdout=stdout, stderr="")
    assert report.limit is not None
    assert report.limit.reset_at == datetime(2027, 3, 20, 15, 36, tzinfo=BERLIN)
    assert report.outcome is None


def test_a_reset_given_only_as_a_time_is_the_next_such_time(tmp_path: Path) -> None:
    event = {"type": "error", "message": "You've hit your usage limit. Try again at 1:15 PM."}
    report = adapter().report(run(tmp_path), exit_code=1, stdout=json.dumps(event), stderr="")
    assert report.limit is not None
    assert report.limit.reset_at == datetime(2026, 10, 31, 13, 15, tzinfo=BERLIN)  # 1:15 PM passed


def test_a_limit_without_a_reset_time_has_none(tmp_path: Path) -> None:
    event = {"type": "turn.failed", "error": {"message": "You have reached the usage limit."}}
    report = adapter().report(run(tmp_path), exit_code=1, stdout=json.dumps(event), stderr="")
    assert report.limit is not None and report.limit.reset_at is None


def test_other_failures_are_errors(tmp_path: Path) -> None:
    event = {"type": "error", "message": "stream disconnected"}
    report = adapter().report(run(tmp_path), exit_code=1, stdout=json.dumps(event), stderr="boom")
    assert report.limit is None
    assert report.error is not None and "stream disconnected" in report.error


def test_unparsable_lines_are_skipped(tmp_path: Path) -> None:
    stdout = "Reading additional input from stdin...\n" + (FIXTURES / "resume.ndjson").read_text(
        encoding="utf-8"
    )
    report = adapter().report(run(tmp_path), exit_code=0, stdout=stdout, stderr="")
    assert report.session is not None
