#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${ROOT}/.venv"
BIN="${VENV}/bin"
if [ ! -d "${BIN}" ] && [ -d "${VENV}/Scripts" ]; then
  BIN="${VENV}/Scripts"
fi

if [ ! -x "${BIN}/github-issue-worker" ] && [ ! -f "${BIN}/github-issue-worker.exe" ]; then
  PYTHON="${PYTHON:-python3}"
  if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    PYTHON="python"
  fi
  if [ ! -x "${VENV}/bin/python" ] && [ ! -f "${VENV}/Scripts/python.exe" ]; then
    "${PYTHON}" -m venv "${VENV}"
  fi
  if [ -d "${VENV}/Scripts" ] && [ ! -d "${VENV}/bin" ]; then
    BIN="${VENV}/Scripts"
  fi
  "${BIN}/pip" install --quiet -e "${ROOT}"
fi

exec "${BIN}/github-issue-worker" watch "$@"
