from __future__ import annotations

import pytest

from github_issue_worker.__main__ import main
from github_issue_worker.product import MARKER_PREFIX, NAME, version


def test_version_comes_from_the_installed_distribution() -> None:
    assert version() == "0.1.0"


def test_marker_is_an_html_comment() -> None:
    assert MARKER_PREFIX.startswith("<!--")


def test_version_flag_prints_name_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"{NAME} 0.1.0\n"


def test_unknown_command_fails_with_usage_exit_code() -> None:
    assert main(["watch"]) == 2
