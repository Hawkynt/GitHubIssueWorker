from __future__ import annotations

import pytest

from github_issue_worker.domain.model_name import display_model


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-haiku-5-5", "Claude Haiku 5.5"),
        ("gpt-6.1-sol", "GPT 6.1 Sol"),
        ("Gemini 4 Argon (Medium)", "Gemini 4 Argon (Medium)"),
        ("Qwen3.8-Flash-Next IQ3_S", "Qwen3.8 Flash Next IQ3_S"),
        ("qwen3.8-flash-next-iq3_s", "Qwen3.8 Flash Next IQ3_S"),
        ("local-claude-strata-iq3_s", "Local Claude Strata IQ3_S"),
        ("Qwen3.8-27B Q4", "Qwen3.8 27B Q4"),
        ("  spaced   out  ", "Spaced Out"),
    ],
)
def test_model_names_are_shown_normalised(raw: str, shown: str) -> None:
    assert display_model(raw) == shown


def test_nothing_known_stays_a_dash() -> None:
    assert display_model(None) == "-"
    assert display_model("") == "-"
