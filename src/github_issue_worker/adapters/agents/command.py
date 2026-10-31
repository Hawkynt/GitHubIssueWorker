"""Any further CLI agent, run from the worker's argument template (F12.7).

The template is an argument list, program first. ``{prompt}``, ``{worktree}``, ``{outcome}`` and
``{session}`` are replaced wherever they occur; nothing else is, so JSON or other braces pass
through. On a fresh run an argument containing ``{session}`` is left out, which is why a flag and
its value belong in one argument (``--resume={session}``).

A usage limit is what the worker's configuration says it is: an exit code in ``limitExitCodes``, or
a failed run (non-zero exit or no outcome) with a line of output matching one of ``limitPatterns``.
Patterns are not read on a successful run, where an agent may well mention a rate limit at work.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from github_issue_worker.adapters.agents import prepare, read_outcome
from github_issue_worker.ports.agents import AgentRun, Invocation, RunReport, UsageLimit

_SESSION = "{session}"
_STDERR_TAIL = 20


class CommandAdapter:
    kind = "command"

    def __init__(
        self,
        template: Sequence[str],
        executable: str | None = None,
        env: dict[str, str] | None = None,
        limit_patterns: Iterable[str] = (),
        limit_exit_codes: Iterable[int] = (),
    ) -> None:
        if not template:
            raise ValueError("a command worker needs a command")
        self._template = list(template)
        self._executable = executable
        self._env = env or {}
        self._patterns = [re.compile(pattern, re.IGNORECASE) for pattern in limit_patterns]
        self._limit_exit_codes = frozenset(limit_exit_codes)

    def invocation(self, run: AgentRun) -> Invocation:
        outcome, _schema = prepare(run)
        values = {
            "{prompt}": run.prompt,
            "{worktree}": str(run.worktree.resolve()),
            "{outcome}": str(outcome.resolve()),
            _SESSION: run.session or "",
        }
        argv = []
        for argument in self._template:
            if _SESSION in argument and not run.session:
                continue
            for placeholder, value in values.items():
                argument = argument.replace(placeholder, value)
            argv.append(argument)
        if self._executable:
            argv[0] = self._executable
        return Invocation(argv=argv, cwd=run.worktree, env=self._env)

    def report(self, run: AgentRun, exit_code: int, stdout: str, stderr: str) -> RunReport:
        session = run.session
        if exit_code in self._limit_exit_codes:
            message = _last_line(stderr) or _last_line(stdout) or f"exit {exit_code}"
            return RunReport(session, None, limit=UsageLimit(message, None))

        outcome, problem = read_outcome(run.worktree) if exit_code == 0 else (None, None)
        if outcome is not None:
            return RunReport(session, outcome)

        matched = self._limit_line(stdout, stderr)
        if matched is not None:
            return RunReport(session, None, limit=UsageLimit(matched, None))
        if exit_code != 0:
            tail = "\n".join(stderr.strip().splitlines()[-_STDERR_TAIL:])
            error = f"exit {exit_code}: {tail}" if tail else f"exit {exit_code}"
            return RunReport(session, None, error=error)
        return RunReport(session, None, error=problem)

    def _limit_line(self, stdout: str, stderr: str) -> str | None:
        for line in (stderr + "\n" + stdout).splitlines():
            if any(pattern.search(line) for pattern in self._patterns):
                return line.strip()
        return None


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1].strip() if lines else ""
