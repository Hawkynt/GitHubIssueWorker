"""The prompt every agent run gets (F6.2, F6.3): the issue, the working contract, the rules."""

from __future__ import annotations

from collections.abc import Iterable

from github_issue_worker.domain.issue import Comment, Issue
from github_issue_worker.domain.work import RunKind

_KIND = {
    RunKind.FIRST: "This is the first run on this issue.",
    RunKind.REWORK: (
        "This is a rework run: the comments below are new input on work done earlier. Continue on "
        "the existing branch and address them."
    ),
    RunKind.REOPEN: (
        "The issue was reopened after it had been closed as done: the earlier change did not "
        "fully solve it. Read the comments for why, and work on top of the current base branch."
    ),
    RunKind.CONTINUATION: (
        "This run continues interrupted work. Read .ghiw/journal.md first if it exists, check the "
        "branch's commits and any uncommitted changes, and carry on from there."
    ),
}

CONTRACT = """\
## How to work

Work in this order, and do not skip a step:

1. **Specification first (SDD).** Read the repository's specification (README, SPEC.md, PRD.md, or
   whatever AGENTS.md names). If the issue changes specified behaviour, amend the specification
   first, in its own commit, and write the code against the amended text.
2. **Scenarios (BDD).** Turn the issue into acceptance scenarios in Given / When / Then form. Each
   one is backed by at least one automated test named after it.
3. **Domain language (DDD).** Use the repository's existing vocabulary, keep domain logic in the
   domain layer, and introduce a new term only when the domain genuinely gained one.
4. **Tests first (TDD).** For each scenario: write a failing test, run it and see it fail, make the
   smallest change that passes, then refactor on green. A bug fix starts with the test that
   reproduces the bug.
5. **Done means verified.** The repository's own build and tests pass. "Looks right" is not done.

Read AGENTS.md, CLAUDE.md and CONTRIBUTING.md in this directory first, if they exist. Where they are
stricter than this text, they win.

## Rules

- Commit your work in this worktree, one concern per commit. Every subject starts with one of
  `+` (added), `-` (removed), `*` (changed), `#` (bug fixed), `!` (critical todo) and a space,
  never with "fix", "bugfix", "changed", "modified" or "updated", and every commit has a body after
  a blank line. No message, comment or file may say or hint that a tool, bot or AI wrote it: no
  Co-Authored-By lines, no "Generated with", no tool or vendor names.
- Do not push, do not open pull requests, do not use `gh` or the GitHub API. You have no access to
  GitHub here; the manager pushes, opens the pull request and merges after checking your work.
- Keep `.ghiw/journal.md` up to date while you work: the scenarios, the decisions you took and why,
  what is done and what is next. Another agent may have to continue from it.
- Stop and ask instead of guessing when the issue has more than one reasonable reading that leads to
  different code, when it needs a large design decision (a new dependency, a breaking public API
  change, a data migration, a new top-level component, a change that contradicts the
  specification), or when it is impossible.

## When you finish

Write `.ghiw/outcome.json`, following the JSON schema in `.ghiw/outcome.schema.json`:

- `outcome`: `done` when the work is committed and verified, `needs-input` when you have to ask,
  `impossible` when it cannot be done;
- `summary`: what you did, or why you stopped, in a few sentences;
- `scenarios`: the acceptance scenarios, one string each;
- `questions`: for `needs-input`, each question with its options and the option you recommend;
- `labels`: see below, otherwise an empty list.
"""


UNDERSTAND = """\
## This run: understand the issue, change nothing

Do not change any file and do not commit in this run. Read the issue, its comments, and as much of
the repository as you need (its specification first), then restate the request so that it can be
built and checked. Issues are often short and not measurable; your restatement is what the work
will be measured against, and it is posted to the issue for the maintainer to correct.

Write `.ghiw/outcome.json` following `.ghiw/outcome.schema.json`:

- `outcome`: `done` when you can restate the request; `needs-input` with questions when it admits
  readings that lead to different code, or needs a large design decision; `impossible` when it
  cannot be done;
- `restatement`: the issue in your own words, in Markdown: the goal in one or two sentences, what
  is in scope and what is not, the acceptance criteria as Given / When / Then scenarios that a test
  can check, and the assumptions you made;
- `scenarios`: the same acceptance scenarios, one string each;
- `summary`: one sentence;
- `labels`: see below, otherwise an empty list;
- `questions`: for `needs-input`, otherwise an empty list.
"""


def build_prompt(
    repository: str,
    branch: str,
    issue: Issue,
    comments: Iterable[Comment],
    kind: RunKind,
    label_choices: Iterable[str] | None = None,
    feedback: str | None = None,
    understanding: str | None = None,
    phase: str = "implement",
) -> str:
    parts = [
        f"# Issue #{issue.number} of {repository}: {issue.title}",
        "",
        f"You are working this GitHub issue in the current directory, a git worktree on branch "
        f"`{branch}`. {_KIND[kind]}",
        "",
        "## The issue",
        "",
        issue.body.strip() or "(no description)",
    ]
    comments = list(comments)
    if comments:
        parts += ["", "## Comments", ""]
        for comment in comments:
            parts += [f"**{comment.author.login}** wrote:", "", comment.body.strip(), ""]

    choices = sorted(label_choices) if label_choices is not None else None
    if understanding:
        parts += [
            "",
            "## Agreed understanding",
            "",
            "This restatement of the issue was posted to it and is what the work is measured "
            "against. Comments above that came later take precedence over it.",
            "",
            understanding.strip(),
        ]
    parts += ["", UNDERSTAND if phase == "understand" else CONTRACT]
    if choices:
        parts += [
            "## Labels",
            "",
            "The issue has no labels yet. Choose one to three that fit from exactly this list and "
            "put them in `labels`: " + ", ".join(f"`{name}`" for name in choices) + ".",
            "",
        ]
    if feedback:
        parts += [
            "## Feedback on your previous attempt",
            "",
            "The manager checked your previous attempt and it is not done yet:",
            "",
            feedback.strip(),
            "",
        ]
    return "\n".join(parts).rstrip() + "\n"
