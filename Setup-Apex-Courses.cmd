@echo off
setlocal
REM Kolibri courses for the Apocalypse library: starts Kolibri inside NOMAD
REM (installing it the first time), lists the courses already imported, opens it
REM in the browser and shows how to import more. Needs Docker Desktop and NOMAD
REM running (Start-Apex-Platform.cmd) and, for imports, the internet.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\setup_apex_apocalypse.py --courses
set "RC=%errorlevel%"
pause
exit /b %RC%
