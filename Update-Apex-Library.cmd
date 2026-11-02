@echo off
setlocal
REM Updates the Apocalypse library to newer versions, one archive at a time.
REM   Update-Apex-Library.cmd                  list what has a newer version, with sizes
REM   Update-Apex-Library.cmd NAME [NAME...]    update those archives
REM   Update-Apex-Library.cmd all               update everything that has a newer version
REM The old copy keeps working until the new one is downloaded and checked, then
REM it is removed. Internet drops: it waits and retries. Ctrl+C stops safely; run
REM the same command again to continue.
cd /d "%~dp0"
if not defined APEX_APOCALYPSE_HOME set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
if "%~1"=="" goto check
set "ARGS="
:collect
if "%~1"=="" goto update
set "ARGS=%ARGS% --update %~1"
shift
goto collect

:check
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py --check-updates
set "RC=%errorlevel%"
pause
exit /b %RC%

:update
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py%ARGS% --until-done
if errorlevel 1 (
  echo Stopped. The old copies are untouched and the partial download is kept: run the same command again to continue.
  pause
  exit /b 1
)
echo Done. Updated archives are downloaded, checksum-verified, and their old copies removed.
pause
exit /b 0
