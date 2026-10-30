@echo off
setlocal

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"

if not exist "%VENV%\Scripts\github-issue-worker.exe" if not exist "%VENV%\Scripts\github-issue-worker.cmd" (
  if not exist "%VENV%\Scripts\python.exe" (
    python -m venv "%VENV%" || exit /b 1
  )
  "%VENV%\Scripts\pip.exe" install --quiet -e "%ROOT%." || exit /b 1
)

call "%VENV%\Scripts\github-issue-worker" watch %*
exit /b %ERRORLEVEL%
