# Contributing

Thanks for helping improve **GitHubIssueWorker**. This guide covers building, testing, and the
conventions every change is expected to follow. Coding agents must also read [`AGENTS.md`](AGENTS.md).

## Prerequisites

- **Python 3.13+** (the CI matrix pins 3.13 on ubuntu and windows).
- `git`, and for the `external_interop`/`e2e` markers also `gh` and `claude` — see the README's
  Installation section.
- `node` is only needed to run the shared README-checker script locally; CI provides it.

## Build

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

The package is `github-issue-worker`, the import name is `github_issue_worker`, the source lives
under `src/`. The CLI is `github-issue-worker` (or `python -m github_issue_worker`).

## Test

```bash
pytest -q                                              # everything
pytest -q -m "not slow and not e2e and not os_integration and not external_interop"   # the fast tier
ruff check . && ruff format --check . && mypy          # lint + types
```

New behaviour is test-first: add the failing test, then make it pass. Keep test data deterministic
(fixed strings, fixed clocks) and build fixtures in setup rather than committing large recordings.
Recorded GitHub responses must not contain tokens or private repository content.

### The two tiers

A test belongs to the **fast tier** unless it says otherwise. That direction is deliberate: marking
every fast test would mean tagging thousands of them and remembering to tag each new one, and the
one somebody forgets silently drops out of the fast tier without anybody noticing. Opting out is a
decision somebody makes once, in the open.

A test opts out by carrying one of these markers:

| Marker | For |
|---|---|
| `slow` | Quick in kind, expensive in practice — a long deadline, a wall-clock wait. The general escape hatch. |
| `e2e` | Drives a real scratch GitHub repository end to end: issue in, merged PR out. |
| `os_integration` | Needs something of the host: signals, a service manager, file locks across processes. |
| `external_interop` | Runs a real `git` or `claude` executable instead of the fake. |

**Opting out defers a test, it never skips one.** Everything runs on the pull request. The tiers
decide *when*, not *whether*.

Two rules that make the difference real:

- **A test in the fast tier finishes in well under a second.** If it does not, either make it so or
  mark it `slow`.
- **The fast tier is offline and free.** No network, no real `claude`, no tokens. A test that needs
  any of them carries the marker that says so.

## Branches, pull requests, merges

- One feature per branch; the branch name describes the feature (`feature/…`).
- Push the branch, open a pull request. The PR runs the **full** battery on both OSes; it must be
  green to merge.
- Merge by **squash**. Direct pushes to `main` are blocked by a ruleset.
- **Every commit is signed** (`gpg.format = ssh`). Set it up once:

  ```bash
  git config gpg.format ssh
  git config user.signingkey ~/.ssh/id_ed25519_github.pub
  git config commit.gpgsign true
  ```

## Commit conventions

- One concern per commit, with a descriptive body citing the roadmap id it serves (`F7.1`).
- Subject lines start with a prefix — `+` added · `-` removed · `*` changed · `#` bug fixed ·
  `!` critical todo. Never begin with "fix"/"changed"/"modified".
- Write everything as if authored by hand: no AI attribution anywhere.

## Code style

- Fully type-annotated, `mypy --strict` clean, frozen dataclasses for value objects, `Protocol`
  for ports.
- `ruff format` output; 4-space indent in Python, 2 in YAML/TOML/JSON (see `.editorconfig`).
- `domain` and `application` do no I/O; adapters do. Every GitHub write goes through the shipping
  service.

## Continuous integration

Workflows live in `.github/workflows/`:

| Workflow | Trigger | Purpose |
|---|---|---|
| `smoke.yml` | push to a working branch | The fast tier and the README check. One OS. Minutes. |
| `ci.yml` | PR to `main` | The gate. Lint, types, every OS, every marker, coverage as a metric, README check. |
| `_build.yml` | called by release/nightly | Shared build step so both paths produce identical artifacts. |
| `nightly.yml` | on merge to `main` | Dated `nightly-YYYYMMDD` prerelease, GFS-pruned. |
| `release.yml` | manual dispatch | Cuts a dated `vYYYYMMDD` release. |

```
push to a working branch  ->  smoke.yml                    fast, one OS
pull request              ->  ci.yml                       the gate: every OS, every marker
push to main              ->  nightly build only           the pull request was already green
```

Every job declares `timeout-minutes`. Coverage is a reporting metric, not a gate. Every marker has
somewhere it runs.

To validate workflow edits, [`actionlint`](https://github.com/rhysd/actionlint) is the recommended
linter:

```bash
actionlint .github/workflows/*.yml
```

### When the repo-README check fails

It reads one file and generates nothing — reproduce it in full with one command:

```bash
curl -sL https://raw.githubusercontent.com/Hawkynt/RepositoryTemplate/v1/scripts/repo-readme.mjs -o /tmp/repo-readme.mjs
node /tmp/repo-readme.mjs --check . --repo Hawkynt/GitHubIssueWorker
```

Every finding names the rule that produced it and the line it sits on. `--write` fixes the badge
block; the rest are prose decisions. The rules and the canonical order are in
[`repo-readme/README.md`](https://github.com/Hawkynt/RepositoryTemplate/blob/main/repo-readme/README.md).

## Releases

Stable releases are cut manually by the maintainer:

```bash
gh workflow run release.yml
```

Never cut a release unless explicitly asked.
