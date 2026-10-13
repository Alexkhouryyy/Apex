@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Apex.bat once to prepare Apex first.
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\setup_screen_companion.py %*
exit /b %ERRORLEVEL%
