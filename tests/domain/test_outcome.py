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
