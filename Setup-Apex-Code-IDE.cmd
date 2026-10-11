@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Set up Apex first. Its Python environment is missing.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\setup_code_ide.py
if errorlevel 1 (
  pause
  exit /b 1
)
pause
