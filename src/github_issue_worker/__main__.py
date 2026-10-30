"""Command-line entry point: ``github-issue-worker`` or ``python -m github_issue_worker``."""

from __future__ import annotations

import sys

from github_issue_worker.product import NAME, version


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(f"{NAME} {version()}")
        return 0

    print(f"{NAME} {version()} — no commands are implemented yet; see README.md.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
