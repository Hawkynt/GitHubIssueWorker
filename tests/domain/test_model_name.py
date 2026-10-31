from __future__ import annotations

import pytest

from github_issue_worker.domain.model_name import (
    display_model,
    model_creator,
    model_family,
)


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


@pytest.mark.parametrize(
    ("name", "creator"),
    [
        ("Claude Opus 5.5", "anthropic"),
        ("claude-opus-4-1", "anthropic"),
        ("Opus", "anthropic"),
        ("GPT 6.1 Sol", "openai"),
        ("gpt-5.1-codex-max", "openai"),
        ("Gemini 4 Argon (Medium)", "google"),
        ("Qwen3.8 Flash Next IQ3_S", "qwen"),
        ("Mistral Large 3", "mistral"),
        ("Llama 3.3 70B Instruct", "meta"),
        ("DeepSeek V3.2", "deepseek"),
        ("Grok 4 Fast", "xai"),
    ],
)
def test_a_model_name_names_the_maker_that_built_it(name: str, creator: str) -> None:
    assert model_creator(name) == creator


@pytest.mark.parametrize("name", ["Strata 8B", "Local Model", "-"])
def test_a_model_nobody_knows_names_no_maker(name: str) -> None:
    assert model_creator(name) == ""


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("Claude Opus 5.5", "opus"),
        ("Claude Opus 5", "opus"),
        ("claude-sonnet-4-5", "sonnet"),
        ("Claude Haiku 5.5", "haiku"),
        ("Fable 5", "fable"),
        ("Opus", "opus"),
        ("GPT 6.1 Sol", "sol"),
        ("Gemini 4 Argon (Medium)", "argon"),
        ("Qwen3.8 Flash Next IQ3_S", "flash next"),
        ("GPT 4", "gpt"),
        ("Strata 8B", "strata"),
    ],
)
def test_the_family_is_the_name_of_the_model_without_maker_and_version(
    name: str, family: str
) -> None:
    assert model_family(name) == family


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("Claude Opus 5", "opus"),
        ("Claude Opus 5.5", "opus"),
        ("Qwen3.8 Flash Next IQ3_S", "flash next"),
        ("Qwen3.8 Flash Next BF16", "flash next"),
    ],
)
def test_the_family_ignores_the_version_and_the_quantisation(name: str, family: str) -> None:
    assert model_family(name) == family
