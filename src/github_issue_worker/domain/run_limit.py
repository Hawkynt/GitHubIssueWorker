"""A run stopped by its own limit, `runTimeout` or the budget, and the comment that parks it (F6.7).

Such a run is not a failure to retry: a second run would only spend the same limit again. The issue
is parked like a question instead, with what was finished and what was not, taken from the commits
on the branch and from the journal the agent keeps (`.ghiw/journal.md`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

RunLimitKind = Literal["time", "budget"]


@dataclass(frozen=True, slots=True)
class RunCutOff:
    """Which limit stopped the run, and its configured value as people read it ("2 h", "$5")."""

    kind: RunLimitKind
    limit: str


def duration_text(seconds: float) -> str:
    """7200 as "2 h", 5400 as "1 h 30 min", 90 as "1 min 30 s"."""
    if seconds < 1:
        return f"{seconds:g} s"
    whole = round(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [(hours, "h"), (minutes, "min"), (secs, "s")]
    return " ".join(f"{value} {unit}" for value, unit in parts if value)


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_BOX = re.compile(r"^\[(?P<mark>[ xX])\]\s+(?P<text>.+)$")
_LEFT = (
    "not done",
    "incomplete",
    "not finished",
    "next",
    "todo",
    "to do",
    "remaining",
    "open",
    "left",
)
_DONE = ("done", "finished", "completed", "complete")


def _section(title: str) -> Literal["finished", "remaining"] | None:
    lowered = title.lower()
    if any(word in lowered for word in _LEFT):
        return "remaining"
    if any(word in lowered for word in _DONE):
        return "finished"
    return None


def journal_progress(journal: str) -> tuple[list[str], list[str]]:
    """What the journal marks as finished and what it has left: the items under headings such as
    "Done" and "Next", and ticked or open checkboxes anywhere else."""
    finished: list[str] = []
    remaining: list[str] = []
    section: Literal["finished", "remaining"] | None = None
    for line in journal.splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = _section(heading["title"])
            continue
        item = _ITEM.match(line)
        text = item["text"] if item else line.strip()
        if not text:
            continue
        box = _BOX.match(text)
        if box:
            ticked = box["mark"] in "xX"
            (finished if ticked else remaining).append(box["text"].strip())
        elif section == "finished":
            finished.append(text)
        elif section == "remaining":
            remaining.append(text)
    return finished, remaining


def _cost(cost_usd: float) -> str:
    return f"${cost_usd:.2f}"


def park_comment(
    cut_off: RunCutOff,
    commits: list[str],
    journal: str | None,
    cost_usd: float | None = None,
) -> str:
    """The one comment that parks an issue whose run hit a limit: which limit, what was finished
    (commit subjects and the journal's done part), what was not, and how to continue."""
    if cut_off.kind == "time":
        reason = f"the run reached its time limit (`runTimeout`, {cut_off.limit})"
        knob = "`runTimeout`"
    else:
        reason = f"the run used up its budget (`maxBudgetUsdPerRun`, {cut_off.limit})"
        knob = "`maxBudgetUsdPerRun`"
    if cost_usd is not None:
        reason += f" after spending {_cost(cost_usd)}"
    text = journal.strip() if journal else ""
    done, left = journal_progress(text)

    lines = [f"I stopped working on this issue: {reason}.", "", "**Finished**", ""]
    finished = [f"`{subject}`" for subject in commits] + done
    if finished:
        lines += [f"- {item}" for item in finished]
    else:
        lines.append(
            "Nothing yet: there is no commit on the branch and the journal marks nothing done."
        )
    lines += ["", "**Not finished**", ""]
    if left:
        lines += [f"- {item}" for item in left]
    elif not text:
        lines.append("The issue itself. There is no journal saying what is left.")
    else:
        lines.append("The issue itself. The journal does not say what is left.")
    if text and not (done or left):
        lines += ["", "<details><summary>Journal</summary>", "", text, "", "</details>"]
    lines += [
        "",
        "Comment here and the work continues from the journal and the commits. If the limit was "
        f"too tight for this issue, raise {knob} first.",
    ]
    return "\n".join(lines)
