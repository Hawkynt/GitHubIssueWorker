from __future__ import annotations

from github_issue_worker.domain.run_limit import (
    RunCutOff,
    duration_text,
    journal_progress,
    park_comment,
)

JOURNAL = """# Journal

## Scenarios
- Given a timeout When retrying Then 3 attempts

## Done
- retry count read from the configuration
- test for three attempts

## Next
- back-off between attempts
- docs
"""


def test_a_duration_reads_in_hours_minutes_and_seconds() -> None:
    assert duration_text(7200) == "2 h"
    assert duration_text(5400) == "1 h 30 min"
    assert duration_text(90) == "1 min 30 s"
    assert duration_text(0.5) == "0.5 s"


def test_the_journal_s_done_and_next_sections_are_what_was_finished_and_what_was_not() -> None:
    finished, remaining = journal_progress(JOURNAL)
    assert finished == ["retry count read from the configuration", "test for three attempts"]
    assert remaining == ["back-off between attempts", "docs"]


def test_journal_checkboxes_outside_those_sections_count_too() -> None:
    finished, remaining = journal_progress("## Plan\n- [x] parser\n- [ ] writer\n* [X] tests\n")
    assert finished == ["parser", "tests"]
    assert remaining == ["writer"]


def test_a_not_done_heading_is_what_is_left_not_what_is_finished() -> None:
    finished, remaining = journal_progress("## Completed\n- a\n\n## Not done yet\n- b\n")
    assert (finished, remaining) == (["a"], ["b"])


def test_a_stopped_run_parks_with_the_limit_what_was_finished_and_what_was_not() -> None:
    body = park_comment(
        RunCutOff("time", "2 h"),
        commits=["+ retry count honoured (F1)", "* client reads the count"],
        journal=JOURNAL,
    )
    assert "time limit" in body and "`runTimeout`" in body and "2 h" in body
    finished, rest = body.split("**Not finished**")
    assert "**Finished**" in finished
    assert "+ retry count honoured (F1)" in finished and "* client reads the count" in finished
    assert "test for three attempts" in finished
    assert "back-off between attempts" in rest and "docs" in rest
    assert "comment" in rest.lower()


def test_a_budget_stop_names_the_budget_and_what_it_cost() -> None:
    body = park_comment(RunCutOff("budget", "$2.50"), commits=[], journal=None, cost_usd=2.5123)
    assert "budget" in body and "`maxBudgetUsdPerRun`" in body and "$2.50" in body
    assert "$2.51" in body


def test_a_stopped_run_with_no_journal_and_no_commit_says_nothing_was_finished() -> None:
    body = park_comment(RunCutOff("time", "2 h"), commits=[], journal=None)
    finished, rest = body.split("**Not finished**")
    assert "Nothing" in finished
    assert "no journal" in rest.lower()
    assert "- \n" not in body and not body.rstrip().endswith("**")


def test_a_journal_without_recognisable_sections_is_quoted_whole() -> None:
    body = park_comment(RunCutOff("time", "2 h"), commits=[], journal="tried X, then Y")
    assert "tried X, then Y" in body and "<details>" in body
