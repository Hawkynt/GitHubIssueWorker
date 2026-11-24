#!/usr/bin/env bash
# Sets up .venv with Python 3.13 or newer and starts `github-issue-worker watch`.
# Every step and the exact command it runs is printed, so a failing start can be debugged.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ROOT}/.venv"

say() { printf '[run.sh] %s\n' "$*"; }
step() {
  say "> $*"
  "$@"
}
venv_bin() {
  if [ -d "${VENV}/Scripts" ] && [ ! -d "${VENV}/bin" ]; then
    printf '%s' "${VENV}/Scripts"
  else
    printf '%s' "${VENV}/bin"
  fi
}
has() { [ -x "$1" ] || [ -f "$1.exe" ]; }

# Runs the given interpreter; sets FOUND to its executable when it is Python 3.13 or newer and
# PROBED to the version it reported. Fails otherwise.
FOUND=""
PROBED=""
probe() {
  local line status rest
  FOUND=""
  PROBED=""
  line="$("$@" -c 'import sys; v=sys.version_info; print("ok" if v >= (3, 13) else "old", ".".join(map(str, v[:3])), sys.executable, sep="|")' 2>/dev/null)" || line=""
  if [ -z "${line}" ]; then
    say "  $*: not found"
    return 1
  fi
  status="${line%%|*}"
  rest="${line#*|}"
  PROBED="${rest%%|*}"
  if [ "${status}" != "ok" ]; then
    say "  $*: Python ${PROBED}, too old"
    return 1
  fi
  FOUND="${rest#*|}"
  say "  $*: Python ${PROBED} at ${FOUND}"
}

find_python() {
  local candidate uv_python
  for candidate in ${PYTHON:+"${PYTHON}"} python3.14 python3.13 python3 python; do
    probe "${candidate}" && return 0
  done
  if command -v uv >/dev/null 2>&1; then
    uv_python="$(uv python find --system '>=3.13' 2>/dev/null)" || uv_python=""
    [ -n "${uv_python}" ] && probe "${uv_python}" && return 0
  fi
  return 1
}

say "repository: ${ROOT}"
say "virtual environment: ${VENV}"

BIN="$(venv_bin)"
if has "${BIN}/python"; then
  say "checking the Python in .venv"
  if ! probe "${BIN}/python"; then
    say ".venv was built with Python ${PROBED:-unknown}, older than 3.13: rebuilding it"
    step rm -rf "${VENV}"
  fi
fi

BIN="$(venv_bin)"
if ! has "${BIN}/github-issue-worker"; then
  if ! has "${BIN}/python"; then
    say "looking for Python 3.13 or newer"
    if ! find_python; then
      say "no Python 3.13 or newer found. Install one from https://www.python.org/downloads/"
      say "(or run: uv python install 3.13), or set PYTHON to the full path of its python"
      exit 1
    fi
    step "${FOUND}" -m venv "${VENV}"
    BIN="$(venv_bin)"
  fi
  say "installing github-issue-worker into .venv"
  step "${BIN}/python" -m pip install -e "${ROOT}"
fi

say "> github-issue-worker watch $*"
exec "${BIN}/github-issue-worker" watch "$@"
