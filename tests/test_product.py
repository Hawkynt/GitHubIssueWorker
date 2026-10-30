from __future__ import annotations

import pytest

from github_issue_worker.__main__ import main
from github_issue_worker.cli import main as cli_main
from github_issue_worker.product import MARKER_PREFIX, NAME, version


def test_version_comes_from_the_installed_distribution() -> None:
    assert version() == "0.1.0"


def test_marker_is_an_html_comment() -> None:
    assert MARKER_PREFIX.startswith("<!--")


def test_version_flag_prints_name_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == f"{NAME} 0.1.0\n"


def test_short_version_flag_prints_what_the_long_one_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    long = capsys.readouterr().out
    assert main(["-V"]) == 0
    assert capsys.readouterr().out == long


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_the_parser_accepts_both_version_flags(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        cli_main(["--verbose", flag])
    assert caught.value.code == 0
    assert capsys.readouterr().out == f"{NAME} 0.1.0\n"


def test_an_unknown_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        main(["no-such-command"])
    assert caught.value.code == 2
