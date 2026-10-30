"""Model names as people read them: ``claude-opus-5-5`` is shown as ``Claude Opus 5.5``."""

from __future__ import annotations

import re

_ACRONYMS = {"gpt": "GPT", "llm": "LLM"}
_QUANTISATION = re.compile(r"^(?:i?q\d[\w.]*|bf16|fp\d+|f\d+)$", re.IGNORECASE)


def display_model(raw: str | None) -> str:
    """Hyphens become spaces, runs of numbers become a dotted version, lowercase words get a
    capital first letter, known acronyms and quantisation tags are written in capitals. Words
    that already carry capitals or digits (``Qwen3.8``, ``(Medium)``) keep their spelling."""
    if not raw or not raw.strip():
        return "-"
    words: list[str] = []
    for token in re.split(r"[\s-]+", raw.strip()):
        if not token:
            continue
        if token.isdigit() and words and re.fullmatch(r"\d+(?:\.\d+)*", words[-1]):
            words[-1] = f"{words[-1]}.{token}"
            continue
        lower = token.lower()
        if lower in _ACRONYMS:
            words.append(_ACRONYMS[lower])
        elif _QUANTISATION.match(token):
            words.append(token.upper())
        elif token[0].islower():
            words.append(token[0].upper() + token[1:])
        else:
            words.append(token)
    return " ".join(words)
