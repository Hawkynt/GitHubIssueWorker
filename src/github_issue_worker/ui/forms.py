"""Forms generated from the configuration schema (F14.8).

Every field comes from the pydantic model itself, so a key added to the schema appears in the UI
without anyone remembering to add it. Submitted values go through the same validator as the file
(F0.3), and only keys that were set before or differ from their default are written back.
"""

from __future__ import annotations

import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel

from github_issue_worker.configuration import ModelColorsSettings, _format_duration

FieldKind = Literal["bool", "number", "text", "duration", "choice", "list", "secret", "env"]

SECRETS = frozenset({"ui.token"})
ACRONYMS = {"Ci": "CI", "ci": "CI", "usd": "USD"}

HELP: dict[str, str] = {
    "manager.name": "Shown in logs, in the fleet view and in claims. Default: the host name.",
    "manager.tags": "Capabilities of this machine, matched against needs:<tag> issue labels.",
    "manager.home": "Home directory gh and git run with. Default: your account's home.",
    "pollInterval": "Time between polls of one repository.",
    "trustedUsers": "Logins trusted in addition to everyone with write access.",
    "ui.port": "Port of this page.",
    "ui.bind": "Address of this page. Anything but loopback needs a token.",
    "ui.token": "Required by every request when set. Leave empty to keep the current one.",
    **{
        f"modelColors.{maker}.hue": "Centre of the segment on the colour wheel, 0 to 359."
        for maker in ModelColorsSettings.model_fields
        if maker != "other"
    },
    **{
        f"modelColors.{maker}.width": "Width in degrees."
        for maker in ModelColorsSettings.model_fields
        if maker != "other"
    },
    "modelColors.other.hue": "Centre of the segment every other maker shares, 0 to 359.",
    "modelColors.other.width": "How wide that segment is; each unlisted maker keeps a part of it.",
    "fleet.claimValidity": "How long a claim holds without renewal.",
    "fleet.claimGrace": "Extra time before another worker may take an unrenewed claim over.",
    "fleet.checkpointInterval": "How often a running issue is checkpointed.",
    "fleet.releaseBrokenAfter": "How long a broken worker may keep its issue.",
    "fleet.maxTakeovers": "Takeovers without progress before an issue is marked failed.",
    "fleet.stopClaimingAt": "Usage share at which a worker takes no new issue.",
    "fleet.localHandOff": "On a usage limit, let an idle worker of this machine continue.",
    "fleet.handOffWhenPausedLongerThan": "Hand a paused issue to other machines after this.",
    "defaults.mode": "all: every trusted issue. labeled: only issues labelled worker.",
    "defaults.baseBranch": "Branch work starts from and merges into. Default: the repository's.",
    "defaults.mergeMethod": "Used when the repository allows it.",
    "defaults.ciRepairAttempts": "Rounds of fixing red CI before asking.",
    "defaults.processBacklog": "Also work issues that were open before the repository was added.",
    "defaults.createMissingLabels": "Let triage create labels the repository does not have.",
    "defaults.maxActiveIssuesPerRepository": (
        "Soft limit of live working and paused claims across all machines, including grace."
    ),
    "defaults.runTimeout": "Longest single agent run.",
    "defaults.checksTimeout": "How long a pull request may wait for checks or a review.",
    "defaults.build": "Build command. Default: detected from the repository.",
    "defaults.test": "Test command. Default: detected from the repository.",
    "defaults.forbiddenWords": "Words commit messages must not contain, as regular expressions.",
    "defaults.maxProseLines": "Body lines a commit may have besides + - * # ! change lines.",
    "defaults.splitIssues": "Let a large issue with independent parts become parallel sub-issues.",
}


@dataclass(frozen=True, slots=True)
class FormField:
    path: str
    """The JSON path of the key, which is also the form field's name."""
    label: str
    kind: FieldKind
    choices: tuple[str, ...] = ()
    optional: bool = False


def _label(name: str) -> str:
    words: list[str] = []
    for char in name:
        if char.isupper() and words:
            words.append(" ")
        words.append(char.lower())
    text = "".join(words)
    text = text[:1].upper() + text[1:]
    return " ".join(ACRONYMS.get(word, word) for word in text.split(" "))


def _unwrap(annotation: Any) -> tuple[Any, bool]:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def fields_of(
    model: type[BaseModel], prefix: str = "", skip: frozenset[str] = frozenset()
) -> list[FormField]:
    """One form field per leaf key of ``model``, recursing into nested sections."""
    found: list[FormField] = []
    for name, info in model.model_fields.items():
        alias = info.alias or name
        if alias in skip:
            continue
        path = f"{prefix}{alias}"
        annotation, optional = _unwrap(info.annotation)
        origin = typing.get_origin(annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found += fields_of(annotation, f"{path}.")
            continue
        if path in SECRETS:
            kind: FieldKind = "secret"
            choices: tuple[str, ...] = ()
        elif annotation is bool:
            kind, choices = "bool", ()
        elif annotation in (int, float):
            kind, choices = "number", ()
        elif annotation is timedelta:
            kind, choices = "duration", ()
        elif origin is Literal:
            kind, choices = "choice", tuple(str(arg) for arg in typing.get_args(annotation))
        elif origin is list:
            kind, choices = "list", ()
        elif origin is dict:
            kind, choices = "env", ()
        else:
            kind, choices = "text", ()
        found.append(FormField(path, _label(alias), kind, choices, optional))
    return found


def display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, timedelta):
        return _format_duration(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def value_at(model: BaseModel, path: str) -> Any:
    current: Any = model
    for part in path.split("."):
        attribute = next(
            (
                name
                for name, info in type(current).model_fields.items()
                if (info.alias or name) == part
            ),
            part,
        )
        current = getattr(current, attribute)
    return current


def is_set(model: BaseModel, path: str) -> bool:
    current: Any = model
    for part in path.split("."):
        attribute = next(
            (
                name
                for name, info in type(current).model_fields.items()
                if (info.alias or name) == part
            ),
            part,
        )
        if attribute not in current.model_fields_set:
            return False
        current = getattr(current, attribute)
    return True


def _convert(field: FormField, raw: str | None) -> Any:
    if field.kind == "bool":
        return raw is not None and raw not in ("", "false", "off")
    text = (raw or "").strip()
    if field.kind == "list":
        return [item.strip() for item in text.replace("\n", ",").split(",") if item.strip()]
    if field.kind == "env":
        pairs = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip():
                pairs[key.strip()] = value.strip()
        return pairs
    if not text:
        return None
    return text


def apply_form(
    current: dict[str, Any],
    fields: list[FormField],
    form: Mapping[str, str],
    defaults: BaseModel,
    model: BaseModel,
) -> dict[str, Any]:
    """``current`` (the explicitly set keys) updated with the submitted values.

    A key is written when it was set before or its new value differs from the default, so saving
    a page never turns every default into an explicit setting.
    """
    result = _copy(current)
    for field in fields:
        raw = form.get(field.path)
        if field.kind == "secret" and not (raw or "").strip():
            continue
        value = _convert(field, raw)
        default = display(value_at(defaults, field.path))
        unchanged_default = display(value) == default or (value is None and default == "")
        if unchanged_default and not is_set(model, field.path):
            continue
        _put(result, field.path, value)
    return result


def _copy(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _copy(value) if isinstance(value, dict) else value for key, value in data.items()}


def _put(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    if value is None:
        target.pop(parts[-1], None)
    else:
        target[parts[-1]] = value
