@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 scripts\run_apex_platform.py --open %*
set "APEX_EXIT=%ERRORLEVEL%"
pause
exit /b %APEX_EXIT%
