@echo off
rem Sets up .venv with Python 3.13 or newer and starts `github-issue-worker watch`.
rem Every step and the exact command it runs is echoed; on failure the window waits for a key.
setlocal EnableExtensions

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "SCRIPTS=%VENV%\Scripts"
set "PY="

echo [run.bat] repository: %ROOT%
echo [run.bat] virtual environment: %VENV%

if not exist "%SCRIPTS%\python.exe" if not exist "%SCRIPTS%\python.cmd" goto :venv_checked
echo [run.bat] checking the Python in .venv
call :probe "%SCRIPTS%\python"
if defined PY goto :venv_checked
echo [run.bat] .venv was built with Python %PROBED%, older than 3.13: rebuilding it
echo [run.bat] ^> rmdir /s /q "%VENV%"
rmdir /s /q "%VENV%" || goto :fail
:venv_checked

if exist "%SCRIPTS%\github-issue-worker.exe" goto :watch
if exist "%SCRIPTS%\github-issue-worker.cmd" goto :watch
if exist "%SCRIPTS%\python.exe" goto :install
if exist "%SCRIPTS%\python.cmd" goto :install

echo [run.bat] looking for Python 3.13 or newer
set "PY="
if defined PYTHON call :probe "%PYTHON%"
if not defined PY call :probe py -3
if not defined PY call :probe python
if not defined PY call :probe python3
if not defined PY call :probe_uv
if not defined PY goto :no_python
echo [run.bat] ^> "%PY%" -m venv "%VENV%"
call "%PY%" -m venv "%VENV%" || goto :fail

:install
echo [run.bat] installing github-issue-worker into .venv
echo [run.bat] ^> "%SCRIPTS%\python" -m pip install -e "%ROOT%."
call "%SCRIPTS%\python" -m pip install -e "%ROOT%." || goto :fail

:watch
echo [run.bat] ^> github-issue-worker watch %*
call "%SCRIPTS%\github-issue-worker" watch %*
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" exit /b 0
echo [run.bat] github-issue-worker exited with code %CODE%
goto :wait

:no_python
echo [run.bat] no Python 3.13 or newer found. Install one from https://www.python.org/downloads/
echo [run.bat] (or run: uv python install 3.13), or set PYTHON to the full path of its python.exe
set "CODE=1"
goto :wait

:fail
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" set "CODE=1"
echo [run.bat] the step above failed with exit code %CODE%

:wait
pause
exit /b %CODE%

rem Runs the given interpreter; sets PY to its executable when it is Python 3.13 or newer and
rem PROBED to the version it reported.
:probe
set "PY="
set "PROBED=unknown"
set "VERDICT="
for /f "tokens=1,2,* delims=|" %%A in ('call %* -c "import sys; v=sys.version_info; print('ok' if v >= (3, 13) else 'old', '.'.join(map(str, v[:3])), sys.executable, sep='|')" 2^>nul') do (
  set "VERDICT=%%A"
  set "PROBED=%%B"
  if "%%A"=="ok" set "PY=%%C"
)
if not defined VERDICT echo [run.bat]   %*: not found
if "%VERDICT%"=="old" echo [run.bat]   %*: Python %PROBED%, too old
if defined PY echo [run.bat]   %*: Python %PROBED% at %PY%
exit /b 0

:probe_uv
where uv >nul 2>&1 || exit /b 0
for /f "delims=" %%P in ('uv python find --system ">=3.13" 2^>nul') do call :probe "%%P"
exit /b 0
