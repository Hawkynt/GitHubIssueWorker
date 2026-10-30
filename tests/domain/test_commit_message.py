from __future__ import annotations

import pytest

from github_issue_worker.domain.commit_message import (
    DEFAULT_FORBIDDEN_WORDS,
    CommitGate,
    Rule,
)

GATE = CommitGate()

BODY = "\n\nThe retry loop gave up after the first timeout; it now honours the configured count."


def rules(message: str, gate: CommitGate = GATE) -> set[Rule]:
    return {violation.rule for violation in gate.check(message)}


@pytest.mark.parametrize("prefix", ["+", "-", "*", "#", "!"])
def test_every_house_prefix_is_accepted(prefix: str) -> None:
    assert rules(f"{prefix} retry on timeout{BODY}") == set()


@pytest.mark.parametrize(
    "subject",
    ["retry on timeout", "+retry on timeout", "~ retry on timeout", " + retry on timeout"],
)
def test_subject_without_a_prefix_and_a_space_is_refused(subject: str) -> None:
    assert Rule.PREFIX in rules(subject + BODY)


@pytest.mark.parametrize("word", ["fix", "Fix", "bugfix", "changed", "modified", "updated"])
def test_subject_never_starts_with_a_banned_verb(word: str) -> None:
    assert Rule.BANNED_VERB in rules(f"# {word} the retry loop{BODY}")


def test_a_banned_verb_later_in_the_subject_is_fine() -> None:
    assert rules(f"# the retry loop is fixed{BODY}") == set()


def test_a_body_is_required() -> None:
    assert Rule.BODY in rules("# retry on timeout")
    assert Rule.BODY in rules("# retry on timeout\n\n   \n")


def test_the_body_must_be_separated_by_a_blank_line() -> None:
    assert Rule.BODY in rules("# retry on timeout\nno blank line before this")


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "co-authored-by: Codex <codex@openai.com>",
        "Co-Authored-By: Gemini <gemini@google.com>",
    ],
)
def test_an_ai_co_author_trailer_is_refused(trailer: str) -> None:
    assert Rule.AI_TRACE in rules(f"# retry on timeout{BODY}\n\n{trailer}")


def test_a_human_co_author_is_fine() -> None:
    message = f"# retry on timeout{BODY}\n\nCo-Authored-By: Jane Doe <jane@example.org>"
    assert rules(message) == set()


@pytest.mark.parametrize(
    "line",
    [
        "Generated with some tool",
        "🤖 made by a bot",
        "Suggested by Claude.",
        "Done with the Codex CLI.",
        "An AI wrote the parser.",
        "Asked the LLM about it.",
        "The agent proposed this.",
        "Agents did the rest.",
        "Anthropic, OpenAI, Gemini, Antigravity and OpenCode",
    ],
)
def test_ai_traces_in_the_body_are_refused(line: str) -> None:
    assert Rule.AI_TRACE in rules(f"# retry on timeout{BODY}\n{line}")


@pytest.mark.parametrize(
    "line",
    ["He said it was fine.", "The maintainer approved.", "Await the main loop.", "rail ai"],
)
def test_words_that_merely_contain_a_trace_are_fine(line: str) -> None:
    # "said", "maintainer", "main" contain "ai"; lower-case "ai" is not the acronym.
    assert rules(f"# retry on timeout{BODY}\n{line}") == set()


def test_the_forbidden_words_are_configurable_per_repository() -> None:
    gate = CommitGate(forbidden_words=tuple(w for w in DEFAULT_FORBIDDEN_WORDS if w != "agents?"))
    assert rules(f"# add the agent adapter{BODY}", gate) == set()
    assert Rule.AI_TRACE in rules(f"# add the agent adapter{BODY}")


def test_violations_name_the_line_they_sit_on() -> None:
    message = f"# retry on timeout{BODY}\nfirst\nGenerated with a tool"
    (violation,) = GATE.check(message)
    assert violation.rule is Rule.AI_TRACE
    assert violation.line == 5
