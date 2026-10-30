"""Identity of the worker as it appears in markers, logs and the command line."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

NAME = "GitHubIssueWorker"
"""The product name."""

DISTRIBUTION = "github-issue-worker"
"""The name the package is installed under."""

MARKER_PREFIX = "<!-- ghiw:"
"""Prefix of the hidden HTML marker the worker puts into every comment it writes, so that it never
mistakes its own comments for new input."""


def version() -> str:
    """Return the installed version, or ``0.0.0`` when running from an uninstalled source tree."""
    try:
        return _distribution_version(DISTRIBUTION)
    except PackageNotFoundError:
        return "0.0.0"
