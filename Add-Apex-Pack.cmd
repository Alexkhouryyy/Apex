@echo off
setlocal
REM Adds offline packs to the Apocalypse library by name and downloads just those.
REM Find names with Find-Apex-Pack.cmd. Everything already downloaded is left as
REM it is. Internet drops: it waits and retries. Ctrl+C stops safely; run the
REM same command again to continue.
REM   Add-Apex-Pack.cmd wikipedia_ar_medicine_maxi
cd /d "%~dp0"
if not defined APEX_APOCALYPSE_HOME set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Give the pack's name, e.g. Add-Apex-Pack.cmd NAME. Find names with Find-Apex-Pack.cmd.
  pause
  exit /b 1
)
set "ARGS="
:collect
if "%~1"=="" goto add
set "ARGS=%ARGS% --add %~1"
shift
goto collect

:add
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py%ARGS% --until-done
if errorlevel 1 (
  echo Stopped. Everything downloaded so far is kept: run the same command again to continue.
  pause
  exit /b 1
)
echo Done. The pack is downloaded and checksum-verified. Rescan the library in NOMAD to read it.
pause
exit /b 0
