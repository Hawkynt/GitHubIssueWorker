"""The outcome contract every agent run ends with (F6.1): ``.ghiw/outcome.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

OutcomeKind = Literal["done", "needs-input", "impossible"]
_KINDS: tuple[OutcomeKind, ...] = ("done", "needs-input", "impossible")

OUTCOME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "summary", "scenarios", "questions", "labels"],
    "properties": {
        "outcome": {"type": "string", "enum": list(_KINDS)},
        "summary": {"type": "string"},
        "scenarios": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "options", "recommendation"],
                "properties": {
                    "text": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "recommendation": {"type": "string"},
                },
            },
        },
    },
}
"""Strict enough for structured output (every property required, nothing additional)."""


class OutcomeError(ValueError):
    """The outcome file is missing, not JSON, or breaks the contract."""


@dataclass(frozen=True, slots=True)
class Question:
    text: str
    options: tuple[str, ...]
    recommendation: str


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: OutcomeKind
    summary: str
    scenarios: tuple[str, ...] = ()
    questions: tuple[Question, ...] = ()
    labels: tuple[str, ...] = ()
    """Labels the agent chose from the repository's existing set, for an unlabelled issue (F4.1)."""

    @staticmethod
    def parse(text: str) -> Outcome:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise OutcomeError(f"outcome is not JSON: {error}") from None
        if not isinstance(data, dict):
            raise OutcomeError("outcome must be a JSON object")

        kind = data.get("outcome")
        if kind not in _KINDS:
            raise OutcomeError(f"outcome must be one of {', '.join(_KINDS)}, not {kind!r}")
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise OutcomeError("outcome needs a non-empty summary")

        questions = tuple(
            Question(
                text=str(question.get("text", "")),
                options=tuple(str(option) for option in question.get("options", [])),
                recommendation=str(question.get("recommendation", "")),
            )
            for question in data.get("questions") or []
        )
        if kind == "needs-input" and not questions:
            raise OutcomeError("a needs-input outcome must ask at least one question")

        scenarios = tuple(str(scenario) for scenario in data.get("scenarios") or [])
        labels = tuple(str(label) for label in data.get("labels") or [])
        return Outcome(
            kind=kind, summary=summary, scenarios=scenarios, questions=questions, labels=labels
        )
