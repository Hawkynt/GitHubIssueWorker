"""The commit gate (F7.1): house rules every commit must satisfy before the manager pushes it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

PREFIXES = ("+", "-", "*", "#", "!")
"""Subject prefixes: added, removed, changed, bug fixed, critical todo."""

BANNED_VERBS = frozenset({"fix", "fixes", "fixed", "bugfix", "changed", "modified", "updated"})
"""Words a subject must never start with; the prefix already says what kind of change it is."""

DEFAULT_FORBIDDEN_WORDS = (
    "claude",
    "anthropic",
    "codex",
    "openai",
    "gemini",
    "antigravity",
    "opencode",
    "llm",
    "agents?",
)
"""Case-insensitive whole words that reveal machine authorship. Regular-expression fragments, so a
repository can drop or add entries."""

_ALWAYS_FORBIDDEN = (
    re.compile(r"\bAI\b"),
    re.compile(r"generated with", re.IGNORECASE),
    re.compile("\N{ROBOT FACE}"),
)


class Rule(StrEnum):
    """The rule a violation broke."""

    PREFIX = "prefix"
    BANNED_VERB = "banned-verb"
    BODY = "body"
    AI_TRACE = "ai-trace"


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken rule, with the 1-based line it sits on."""

    rule: Rule
    line: int
    detail: str


@dataclass(frozen=True, slots=True)
class CommitGate:
    """Checks commit messages against the house rules."""

    forbidden_words: tuple[str, ...] = DEFAULT_FORBIDDEN_WORDS

    def check(self, message: str) -> list[Violation]:
        """Return every violation in ``message``; an empty list means the commit may be pushed."""
        lines = message.split("\n")
        subject = lines[0]
        violations = self._check_subject(subject)
        violations += self._check_body(lines)
        violations += self._check_traces(lines)
        return violations

    def _check_subject(self, subject: str) -> list[Violation]:
        if len(subject) < 3 or subject[0] not in PREFIXES or subject[1] != " ":
            return [
                Violation(
                    Rule.PREFIX,
                    1,
                    f"subject must start with one of {' '.join(PREFIXES)} and a space",
                )
            ]

        first_word = subject[2:].split(maxsplit=1)[0].lower() if subject[2:].strip() else ""
        if first_word in BANNED_VERBS:
            return [Violation(Rule.BANNED_VERB, 1, f"subject must not start with {first_word!r}")]

        return []

    @staticmethod
    def _check_body(lines: list[str]) -> list[Violation]:
        if len(lines) < 3 or lines[1].strip() or not any(line.strip() for line in lines[2:]):
            return [
                Violation(
                    Rule.BODY, 2, "a body separated from the subject by a blank line is required"
                )
            ]

        return []

    def _check_traces(self, lines: list[str]) -> list[Violation]:
        patterns = [*_ALWAYS_FORBIDDEN]
        if self.forbidden_words:
            words = "|".join(self.forbidden_words)
            patterns.append(re.compile(rf"\b(?:{words})\b", re.IGNORECASE))

        violations = []
        for number, line in enumerate(lines, start=1):
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    violations.append(
                        Violation(
                            Rule.AI_TRACE, number, f"reveals machine authorship: {match.group(0)!r}"
                        )
                    )
                    break

        return violations
