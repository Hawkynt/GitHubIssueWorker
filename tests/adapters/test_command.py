from __future__ import annotations

import json
import sys
from pathlib import Path

from github_issue_worker.adapters.agents.command import CommandAdapter
from github_issue_worker.configuration import WorkerSettings, parse
from github_issue_worker.ports.agents import OUTCOME_FILE, PROMPT_FILE, AgentRun
from github_issue_worker.wiring import execute, make_adapter

TEMPLATE = ["mycli", "--cwd", "{worktree}", "--msg", "{prompt}", "--out", "{outcome}"]


def run(tmp_path: Path, session: str | None = None) -> AgentRun:
    return AgentRun(worktree=tmp_path, prompt="Fix issue 42.", session=session)


def write_outcome(worktree: Path) -> None:
    (worktree / OUTCOME_FILE).parent.mkdir(parents=True, exist_ok=True)
    (worktree / OUTCOME_FILE).write_text(
        json.dumps({"outcome": "done", "summary": "Fixed."}), encoding="utf-8"
    )


def test_the_template_s_placeholders_become_prompt_worktree_and_outcome_path(
    tmp_path: Path,
) -> None:
    (tmp_path / OUTCOME_FILE).parent.mkdir(parents=True)
    (tmp_path / OUTCOME_FILE).write_text("stale", encoding="utf-8")
    invocation = CommandAdapter(TEMPLATE).invocation(run(tmp_path))
    assert invocation.argv == [
        "mycli",
        "--cwd",
        str(tmp_path.resolve()),
        "--msg",
        "Fix issue 42.",
        "--out",
        str((tmp_path / OUTCOME_FILE).resolve()),
    ]
    assert invocation.cwd == tmp_path
    assert invocation.stdin == ""
    assert (tmp_path / PROMPT_FILE).read_text(encoding="utf-8") == "Fix issue 42."
    assert (tmp_path / ".ghiw" / "outcome.schema.json").exists()
    assert not (tmp_path / OUTCOME_FILE).exists()


def test_a_placeholder_inside_an_argument_is_replaced_and_other_braces_stay(
    tmp_path: Path,
) -> None:
    template = ["cli", "--dir={worktree}", '{"a":1}', "{model}", "{prompt}!"]
    argv = CommandAdapter(template).invocation(run(tmp_path)).argv
    assert argv == ["cli", f"--dir={tmp_path.resolve()}", '{"a":1}', "{model}", "Fix issue 42.!"]


def test_placeholders_inside_the_prompt_text_are_passed_unchanged(tmp_path: Path) -> None:
    prompt = "Document {worktree}, {outcome} and {session}."
    adapter = CommandAdapter(["cli", "{prompt}"])
    argv = adapter.invocation(AgentRun(worktree=tmp_path, prompt=prompt, session="s")).argv
    assert argv == ["cli", prompt]


def test_the_program_comes_from_the_installation_when_one_is_given(tmp_path: Path) -> None:
    adapter = CommandAdapter(["mycli", "{prompt}"], executable="/opt/mycli/bin/mycli")
    assert adapter.invocation(run(tmp_path)).argv == ["/opt/mycli/bin/mycli", "Fix issue 42."]


def test_the_installation_env_is_set_for_the_run(tmp_path: Path) -> None:
    adapter = CommandAdapter(["mycli"], env={"MYCLI_HOME": "/p"})
    assert adapter.invocation(run(tmp_path)).env == {"MYCLI_HOME": "/p"}


def test_a_continued_session_fills_the_session_placeholder(tmp_path: Path) -> None:
    adapter = CommandAdapter(["cli", "--resume={session}", "{prompt}"])
    argv = adapter.invocation(run(tmp_path, session="abc")).argv
    assert argv == ["cli", "--resume=abc", "Fix issue 42."]
    write_outcome(tmp_path)
    assert adapter.report(run(tmp_path, session="abc"), 0, "", "").session == "abc"


def test_a_fresh_run_leaves_out_every_argument_with_the_session_placeholder(
    tmp_path: Path,
) -> None:
    adapter = CommandAdapter(["cli", "--resume={session}", "{prompt}", "{session}"])
    assert adapter.invocation(run(tmp_path)).argv == ["cli", "Fix issue 42."]
    write_outcome(tmp_path)
    assert adapter.report(run(tmp_path), 0, "", "").session is None


def test_exit_0_with_a_valid_outcome_is_a_finished_run(tmp_path: Path) -> None:
    adapter = CommandAdapter(TEMPLATE)
    adapter.invocation(run(tmp_path))
    write_outcome(tmp_path)
    report = adapter.report(run(tmp_path), 0, "working\n", "")
    assert report.outcome is not None and report.outcome.kind == "done"
    assert report.limit is None and report.error is None


def test_exit_0_without_an_outcome_is_an_error(tmp_path: Path) -> None:
    adapter = CommandAdapter(TEMPLATE)
    adapter.invocation(run(tmp_path))
    report = adapter.report(run(tmp_path), 0, "", "")
    assert report.outcome is None and report.limit is None
    assert report.error is not None and "no outcome" in report.error


def test_a_configured_limit_exit_code_is_a_usage_limit(tmp_path: Path) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_exit_codes=[75])
    report = adapter.report(run(tmp_path), 75, "", "Too many requests\n")
    assert report.limit is not None and report.limit.reset_at is None
    assert "Too many requests" in report.limit.message
    assert report.error is None and report.outcome is None


def test_a_limit_exit_code_is_a_limit_even_with_an_outcome(tmp_path: Path) -> None:
    write_outcome(tmp_path)
    report = CommandAdapter(TEMPLATE, limit_exit_codes=[75]).report(run(tmp_path), 75, "", "")
    assert report.limit is not None and report.limit.message == "exit 75"


def test_a_limit_pattern_on_a_failed_run_is_a_usage_limit_matched_case_insensitively(
    tmp_path: Path,
) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_patterns=["quota exceeded"])
    report = adapter.report(run(tmp_path), 1, "", "starting\nError: Quota Exceeded, try later\n")
    assert report.limit is not None
    assert report.limit.message == "Error: Quota Exceeded, try later"
    assert report.limit.reset_at is None and report.error is None


def test_a_limit_pattern_is_also_read_from_stdout(tmp_path: Path) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_patterns=[r"rate.?limit"])
    report = adapter.report(run(tmp_path), 1, "hit the RATE-LIMIT\n", "")
    assert report.limit is not None and report.limit.message == "hit the RATE-LIMIT"


def test_a_limit_pattern_on_a_run_with_exit_0_but_no_outcome_is_a_usage_limit(
    tmp_path: Path,
) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_patterns=["usage limit"])
    report = adapter.report(run(tmp_path), 0, "You reached your usage limit\n", "")
    assert report.limit is not None


def test_a_limit_pattern_in_the_output_of_a_successful_run_is_ignored(tmp_path: Path) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_patterns=["rate limit"])
    adapter.invocation(run(tmp_path))
    write_outcome(tmp_path)
    report = adapter.report(run(tmp_path), 0, "handling the rate limit in the client\n", "")
    assert report.limit is None and report.error is None
    assert report.outcome is not None


def test_another_non_zero_exit_is_an_error_with_the_exit_code_and_the_end_of_stderr(
    tmp_path: Path,
) -> None:
    adapter = CommandAdapter(TEMPLATE, limit_patterns=["quota"], limit_exit_codes=[75])
    stderr = "".join(f"line {n}\n" for n in range(100)) + "Traceback: boom\n"
    report = adapter.report(run(tmp_path), 2, "", stderr)
    assert report.limit is None and report.outcome is None
    assert report.error is not None
    assert "exit 2" in report.error and "Traceback: boom" in report.error
    assert "line 0\n" not in report.error


FAKE_CLI = """
import json, pathlib, sys
outcome, prompt, worktree, *rest = sys.argv[1:]
pathlib.Path(worktree, "seen.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if prompt == "limit":
    print("Quota exceeded for today", file=sys.stderr)
    sys.exit(3)
result = {"outcome": "done", "summary": prompt}
pathlib.Path(outcome).write_text(json.dumps(result), encoding="utf-8")
"""


def fake_cli_worker(tmp_path: Path) -> WorkerSettings:
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    body = {
        "name": "fake",
        "agent": "command",
        "account": "a",
        "installation": {"type": "executable", "path": sys.executable},
        "command": [
            "python",
            "-I",
            str(script),
            "{outcome}",
            "{prompt}",
            "{worktree}",
            "{session}",
        ],
        "limitPatterns": ["quota exceeded"],
    }
    return parse(json.dumps({"accounts": {"a": {}}, "workers": [body]})).workers[0]


def test_a_fake_cli_gets_the_substituted_arguments_and_its_outcome_is_the_run_s(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    adapter = make_adapter(fake_cli_worker(tmp_path), tmp_path)
    report = execute(adapter, AgentRun(worktree, "Fix it, with spaces."), timeout=30)
    seen = json.loads((worktree / "seen.json").read_text(encoding="utf-8"))
    assert seen == [
        str((worktree / OUTCOME_FILE).resolve()),
        "Fix it, with spaces.",
        str(worktree.resolve()),
    ]
    assert report.error is None and report.limit is None
    assert report.outcome is not None and report.outcome.summary == "Fix it, with spaces."


def test_a_fake_cli_that_reports_a_limit_ends_its_run_with_a_usage_limit(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    adapter = make_adapter(fake_cli_worker(tmp_path), tmp_path)
    report = execute(adapter, AgentRun(worktree, "limit", session="s-1"), timeout=30)
    assert json.loads((worktree / "seen.json").read_text(encoding="utf-8"))[-1] == "s-1"
    assert report.limit is not None and report.limit.message == "Quota exceeded for today"
    assert report.session == "s-1"
