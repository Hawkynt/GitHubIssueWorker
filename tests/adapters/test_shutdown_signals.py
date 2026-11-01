from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.external_interop
@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal process-group signals")
@pytest.mark.parametrize("tool", ["claims", "github", "workspace"])
def test_terminal_interrupt_does_not_abort_an_in_flight_manager_request(
    tmp_path: Path, tool: str
) -> None:
    # An isolated manager receives the same terminal SIGINT as its children. Its handler
    # requests a drain; the already-running request must finish rather than report an error.
    child = tmp_path / "request.py"
    child.write_text(
        "import os, signal\n"
        "os.killpg(int(os.environ['MANAGER_GROUP']), signal.SIGINT)\n"
        "print('completed', flush=True)\n",
        encoding="utf-8",
    )
    manager = tmp_path / "manager.py"
    manager.write_text(
        "import os, signal, subprocess, sys\n"
        "from pathlib import Path\n"
        "from github_issue_worker.adapters.git_claims import GitClaimStore\n"
        "from github_issue_worker.adapters.gh_api import run_gh\n"
        "from github_issue_worker.adapters.workspace import Workspace\n"
        "os.environ['MANAGER_GROUP'] = str(os.getpgrp())\n"
        "stopped = []\n"
        "signal.signal(signal.SIGINT, lambda *args: stopped.append(True))\n"
        "original = subprocess.run\n"
        "def request(argv, **kwargs):\n"
        "    return original([sys.executable, sys.argv[1]], **kwargs)\n"
        "subprocess.run = request\n"
        "if sys.argv[2] != 'github':\n"
        "    if sys.argv[2] == 'claims':\n"
        "        result = GitClaimStore(Path.cwd())._run('fetch')\n"
        "    else:\n"
        "        workspace = Workspace(Path.cwd(), lambda repo: repo, Path.home())\n"
        "        result = workspace._try(Path.cwd(), 'fetch')\n"
        "    code, output = result.returncode, result.stdout\n"
        "else:\n"
        "    code, output, error = run_gh(['gh'], None, dict(os.environ))\n"
        "assert stopped, 'the manager did not receive SIGINT'\n"
        "assert code == 0, f'request interrupted: {code}'\n"
        "assert output.strip() == 'completed'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(manager), str(child), tool],
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
