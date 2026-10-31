"""Model names as people read them and as the cockpit's colours read them.

``claude-opus-5-5`` is shown as ``Claude Opus 5.5``; the same name read the other way says which
maker built it (``anthropic``), which model of theirs it is (``opus``) and which version of that
model (``5.5``) — the colour follows the first two and only the shade the last.
"""

from __future__ import annotations

import re

_ACRONYMS = {"gpt": "GPT", "llm": "LLM"}
_QUANTISATION = re.compile(r"^(?:i?q\d[\w.]*|bf16|fp\d+|f\d+)$", re.IGNORECASE)

_MAKERS = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "fable": "anthropic",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "codex": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "google": "google",
    "gemini": "google",
    "gemma": "google",
    "mistral": "mistral",
    "mixtral": "mistral",
    "codestral": "mistral",
    "pixtral": "mistral",
    "devstral": "mistral",
    "magistral": "mistral",
    "ministral": "mistral",
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "nvidia": "nvidia",
    "nemotron": "nvidia",
    "meta": "meta",
    "llama": "meta",
    "deepseek": "deepseek",
    "xai": "xai",
    "grok": "xai",
    "microsoft": "microsoft",
    "phi": "microsoft",
    "cohere": "cohere",
    "zai": "zai",
    "glm": "zai",
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "amazon": "amazon",
    "nova": "amazon",
    "ibm": "ibm",
    "granite": "ibm",
    "tii": "tii",
    "falcon": "tii",
    "ai2": "ai2",
    "olmo": "ai2",
}
"""Every word that says which maker built a model, its brand or one of its model lines."""

_BRANDS = frozenset(
    word
    for word, maker in _MAKERS.items()
    if word == maker
    or word in {"claude", "gpt", "chatgpt", "gemini", "gemma", "llama", "grok", "qwen"}
)
"""The words that name the maker rather than the model: a brand is never part of the family."""


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


def _words(raw: str) -> list[tuple[str, str, bool]]:
    """Per word of a name: its leading letters, what follows them, and whether brackets hold it."""
    entries: list[tuple[str, str, bool]] = []
    for token in re.split(r"[\s-]+", raw.strip()):
        if not token:
            continue
        bracketed = bool(re.fullmatch(r"[(\[{].*[)\]}]", token))
        clean = token.strip("()[]{}").strip(".,:;")
        if not clean:
            continue
        head = re.match(r"[A-Za-z]*", clean)
        letters = head.group(0) if head else ""
        entries.append((letters.lower(), clean[len(letters) :], bracketed))
    return entries


def _is_variant(clean: str, head: str, bracketed: bool) -> bool:
    """A version, a size, a quantisation tag or a qualifier: none of them names the model."""
    quantised = bool(_QUANTISATION.match(clean))
    return quantised or clean[0].isdigit() or (bracketed and head not in _MAKERS)


def model_creator(raw: str | None) -> str:
    """Who built it — ``anthropic`` for ``Claude Opus 5.5``; ``""`` when nobody is known."""
    return _read(raw)[0]


def model_family(raw: str | None) -> str:
    """Which model of theirs it is — ``opus``, ``flash next`` — without maker or version."""
    return _read(raw)[1]


def _read(raw: str | None) -> tuple[str, str, str]:
    """``(maker, family, variant)`` of a model name, every part lowercased and space-joined."""
    if not raw:
        return "", "", ""
    maker = ""
    brand = ""
    family: list[str] = []
    variant: list[str] = []
    for head, tail, bracketed in _words(raw):
        word = f"{head}{tail}".lower()
        if _is_variant(word, head, bracketed):
            variant.append(word)
            continue
        named = word if word in _MAKERS else head
        maker_of_word = _MAKERS.get(named)
        if not maker_of_word:
            family.append(word)
            continue
        maker = maker or maker_of_word
        if named in _BRANDS:
            brand = brand or named
        else:
            family.append(named)
        if tail:
            variant.append(tail.lower())
    return maker, " ".join(family) or brand, " ".join(variant)
