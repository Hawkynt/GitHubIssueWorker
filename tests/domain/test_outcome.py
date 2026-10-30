from __future__ import annotations

import pytest

from github_issue_worker.domain.outcome import OUTCOME_SCHEMA, Outcome, OutcomeError


def test_a_full_outcome_parses() -> None:
    outcome = Outcome.parse(
        '{"outcome": "needs-input", "summary": "Two readings.", "scenarios": ["Given x"],'
        ' "questions": [{"text": "Which?", "options": ["a", "b"], "recommendation": "a"}]}'
    )
    assert outcome.kind == "needs-input"
    assert outcome.scenarios == ("Given x",)
    assert outcome.questions[0].options == ("a", "b")
    assert outcome.questions[0].recommendation == "a"


def test_scenarios_and_questions_are_optional_when_parsing() -> None:
    # Agents without native schema support may leave them out; the contract still holds.
    outcome = Outcome.parse('{"outcome": "done", "summary": "Created hello.txt."}')
    assert outcome.kind == "done" and outcome.scenarios == () and outcome.questions == ()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"summary": "no outcome"}',
        '{"outcome": "maybe", "summary": "x"}',
        '{"outcome": "done"}',
        '{"outcome": "needs-input", "summary": "x", "questions": []}',
    ],
)
def test_a_broken_outcome_is_rejected(text: str) -> None:
    with pytest.raises(OutcomeError):
        Outcome.parse(text)


def test_the_schema_is_strict_enough_for_codex_structured_output() -> None:
    # Strict structured output wants every property required and no additional properties, at
    # every level.
    def check(schema: dict) -> None:  # type: ignore[type-arg]
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                check(child)
        if schema.get("type") == "array":
            check(schema["items"])

    check(OUTCOME_SCHEMA)


def test_sub_issues_parse_with_their_titles_bodies_and_scenarios() -> None:
    outcome = Outcome.parse(
        '{"outcome": "done", "summary": "Two independent parts.", "restatement": "R",'
        ' "subIssues": [{"title": "Export CSV", "body": "Add CSV.", "scenarios": ["Given a"]},'
        ' {"title": "Export JSON", "body": "Add JSON.", "scenarios": []}]}'
    )
    assert [sub.title for sub in outcome.sub_issues] == ["Export CSV", "Export JSON"]
    assert outcome.sub_issues[0].body == "Add CSV."
    assert outcome.sub_issues[0].scenarios == ("Given a",)


def test_sub_issues_are_optional_when_parsing() -> None:
    assert Outcome.parse('{"outcome": "done", "summary": "x"}').sub_issues == ()


def test_a_sub_issue_without_a_title_is_rejected() -> None:
    with pytest.raises(OutcomeError):
        Outcome.parse(
            '{"outcome": "done", "summary": "x",'
            ' "subIssues": [{"title": " ", "body": "b", "scenarios": []}]}'
        )


def test_the_schema_asks_for_sub_issues() -> None:
    items = OUTCOME_SCHEMA["properties"]["subIssues"]["items"]
    assert set(items["required"]) == {"title", "body", "scenarios"}
