# Agent guide — GitHubIssueWorker

Working agreement for **all** coding agents (Claude Code, Codex, Copilot, …) and human contributors
in this repository. These rules are not optional. This file is the per-repo distillation of the
house standard.

## What this is

A Python (3.13+) CLI/daemon with a local web UI that watches GitHub repositories the maintainer can
push to and works their issues end to end: label, branch, implement, commit, pull request, merge,
close. A **manager** (one process per machine) steers several **workers**, each one coding agent
(`claude`, `codex`, `agy`, `opencode`, any configured command) working at most one issue at a time,
and does everything outward-facing itself through `git` and the GitHub REST/GraphQL API. Managers on
different machines coordinate through compare-and-swap claims on hidden `refs/ghiw/*` refs in the
watched repositories (valid 7 days after the last renewal, free after 8); there is no server. The UI
is FastAPI + uvicorn on the manager's own event loop, loopback-only by default.
**Every configuration key is editable in the UI**: the file, the CLI and the UI share one validator
and apply changes live, and a key added to the schema without a UI field fails the tests. Package `github-issue-worker`, import name `github_issue_worker`, source under
`src/github_issue_worker/` (`domain · application · ports · adapters · cli`), tests under `tests/`.

**The README is a forward-looking specification and the product requirements document** — it is
authoritative. Its `## 🗺️ Roadmap` checklist is the implementation tracker. When code and spec
disagree, the spec wins unless the spec itself is being revised in the same change; cite the
requirement id (e.g. `F7.1`) in the commit body. Tick an item only when its acceptance criterion is
covered by a passing test or, for operational items, demonstrated on a real repository. Never
renumber or delete an unticked item.

## Relocated homes (ClaudeCodePortable, ClaudeLocal)

Sessions started through ClaudeCodePortable or ClaudeLocal run with `HOME` relocated into a profile
directory. There `gh` is not logged in, so anything that needs `gh` or GitHub over ssh must run with
the account's real home: `HOME=<real home> gh ...`, `HOME=<real home> pytest -m e2e`. The manager
does this by itself (`adapters/home.py`, `manager.home`). A run the manager starts for an agent has
no GitHub access at all, deliberately: such an agent runs the fast tier, and leaves pushing, pull
requests and the `e2e` tests to the manager and CI.

## Commits

- **Group changes semantically** — one requirement/concern per commit, with a detailed body.
- **Every subject line starts with a prefix**: `+` added · `-` removed · `*` changed · `#` bug fixed
  · `!` critical todo.
- Never start a subject with "fix"/"bugfix"/"changed"/"modified"/"updated".
- **Every commit is signed** (`gpg.format = ssh`, the maintainer's GitHub signing key). An unsigned
  commit cannot merge once signature protection is on.
- **No AI traces anywhere**: no `Co-Authored-By` AI lines, no "Generated with" footers, no agent
  mentions in messages, comments, authorship, or docs. Author identity is the maintainer's own.

## Branches and pull requests

- **One feature per branch.** A feature never lands by pushing to `main`.
- Open a pull request for every feature branch; the PR runs the **full** test battery (`ci.yml`)
  and must be green before it can merge.
- Merge by **squash** only. The squashed subject carries the branch's intent plus the PR number.

## The loop (always, in this order)

1. **Before committing**: `ruff check . && ruff format --check . && mypy && pytest -q` until green
   (CI runs the same on ubuntu + windows). New behaviour is test-first (TDD): add the failing test,
   then make it pass. While iterating, the fast tier is
   `pytest -q -m "not slow and not e2e and not os_integration and not external_interop"`; run the
   whole suite before you push.
2. **Commit** (rules above) and **push** to the feature branch.
3. **Open the PR, wait for CI**; merge on green. On `main` a merge triggers the nightly (dated
   prerelease + GFS prune). Fix and loop until everything is green.

Stable releases are **manual** (`gh workflow run release.yml`) — never cut one unless explicitly
asked.

## Tests come in two tiers

A test is in the **fast tier** unless it says otherwise — that direction, never the other, because
the fast test somebody forgets to tag would drop out silently. A test opts out with `slow` (quick
in kind, expensive in practice) or with one of the markers that are slow by nature: `e2e` (a real
GitHub repository), `os_integration`, `external_interop` (a real `git` or agent executable).

Opting out **defers** a test; it never skips one. The pull request runs everything.

- A fast-tier test finishes in well under a second. If yours does not, make it so or mark it `slow`.
- Fast-tier tests never touch the network, never spend money and never call a real agent: GitHub is
  faked at the port or replayed from recorded fixtures, agents are fake executables replaying
  captured vendor output, and claims run against a local bare git remote.
- **Changing a test marker filter means checking every marker still has a step that runs it.** A
  marker excluded from the main filter with no step of its own executes nowhere, and nothing
  reports that.

## Sourcing an implementation

Never write a protocol client, a parser or a format out of your own understanding when somebody has
already got it right. Work **down** this ladder, stop at the first rung that applies,
and say in the commit body which rung you used and why the ones above it did not.

**1 — Licence-compatible source you can take.** MIT, BSD, Apache-2.0, LGPL, public domain: anything
this repository's LGPL-3.0-or-later can absorb. Search for it before writing anything. There are two
ways to take it and the choice is not cosmetic:

- **Vendor it** — a verbatim subtree under `vendored/<Library>/` next to its own `LICENSE.txt`, kept
  in the upstream's own formatting. Do *not* restyle it: the whole point is that the next upstream
  version still applies cleanly. Keep it out of the published API surface rather than by editing the
  source.
- **Convert it** — carry the algorithm across into this codebase properly. Converted code is *our*
  code, so every rule under "Code conventions" applies to it. A conversion that still reads like a
  decompiler's output is not finished.

Either way, record where it came from — a `THIRD-PARTY-NOTICE.<Name>.txt` beside the code.
Attribution is a licence term, not a courtesy.

**2 — Licence-incompatible source: use it, but not its code.** GPL where we ship LGPL, anything
proprietary, anything with no licence at all. Read it and *build material from it*: a written
specification, a set of test cases, and a third-party oracle you can run to produce expected output.
Then implement from that derived material. Do not paste it, do not transliterate it line by line,
and do not carry its file layout or its identifier names across — that is still the same copy.

**Constants are not expression.** Tables, magic numbers, CRC polynomials, Huffman code tables,
quantisation matrices, window and filter coefficients: copy them exactly, from whichever source is
authoritative, on every rung of this ladder. A re-derived S-box is simply a wrong S-box. Where a
value is arbitrary-but-agreed, matching it *is* the specification.

**3 — Original reference material.** The specification, the standard (RFC, ITU-T, ISO, ECMA), the
academic paper, the vendor's own documentation, the format author's write-up. Prefer the normative
text over anybody's description of it; where the two disagree, the normative text wins and the
disagreement is worth a comment.

**4 — Other trusted sources.** Reverse-engineering write-ups, articles and blog posts by named
people with a track record, and long-lived project wikis that cite their evidence.

**5 — Untrusted material, by agreement only.** Forum answers, unattributed gists, wiki edits with no
provenance. Only when nothing above exists, and only where several *independent* sources agree —
majority vote, discounting the ones that plainly copied each other. Treat the result as a hypothesis
and mark it as one in the code.

Whatever rung you land on, the finished implementation is judged the same way: it must agree with an
oracle or with real files, not merely type-check and look plausible. When a licence-incompatible
implementation was your oracle, keep the comparison as a test wherever it can run, and where it
cannot, commit the captured expected output with a note saying what produced it.

## Code conventions

- Fully type-annotated; `mypy --strict` clean. Frozen dataclasses for value objects, `Protocol`
  for ports, `from __future__ import annotations` at the top of every module.
- `ruff format` is the formatter; `ruff check` with the configured rule set is the linter.
  Warnings-as-errors by default — do not suppress without justification in the commit body.
- Ports and adapters: `domain` and `application` do no I/O. Every GitHub write goes through the one
  shipping service, which is where dry-run, the commit gate and the comment marker live — no other
  path may write to GitHub.
- Every step of an issue run is idempotent and checkpointed, so a crash or a takeover by another
  instance at any point resumes rather than repeats.
- The claim is the only lock. Claims change only by `git push --force-with-lease` compare-and-swap
  (the REST ref-update endpoint does not enforce fast-forward and must not be used for them), and
  lease times come from GitHub's `Date` header, never the local clock.
- Versions come from files, never git tags: the package version lives in `pyproject.toml`. The
  repo-level marker is the date: releases tag `vYYYYMMDD`, nightlies `nightly-YYYYMMDD`. This repo
  carries no `scripts/` — the shared tooling lives in `Hawkynt/RepositoryTemplate` and is reached
  through composite actions.

## README & repo conventions

- Standard frame: title → grouped shields.io badges → one-line `>` blockquote → body →
  `## ❤️ Support` (Sponsors + PayPal, mirrors `.github/FUNDING.yml`) → `## 📜 License`. The body is
  a funnel: Vision and Features say what the thing is, Installation and Quick start get the reader
  running, the free band goes deeper, and what a *contributor* needs comes last.
- **The canonical section order and the emoji vocabulary live in
  [`repo-readme/README.md`](https://github.com/Hawkynt/RepositoryTemplate/blob/main/repo-readme/README.md),
  and the `repo-readme` action enforces them.** `## 📜 License` is the last heading in the file.
- **The local UI is a GUI under the house rules.** Every page has a deterministic demo scenario
  (`github-issue-worker ui --demo <scenario>`) that renders the real UI code with plausible, varied
  sample data, and `generate.yml` captures every page as a screenshot on each working-branch push.
  The pull request that lands the first page adds the hero image and the `## 🖼️ Screenshots` tour
  and sets `gui: true` on the README check; a later page lands with its scenario and screenshot in
  the same change.
- Agents are reached through installations: `path` must always work on its own, and the
  ClaudeCodePortable / ClaudeLocal presets are optional extras, never a requirement.
- License is LGPL-3.0-or-later (full `LICENSE`); no per-file license headers in `.py` files.
