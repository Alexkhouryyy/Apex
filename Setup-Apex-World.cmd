@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Apex Python environment is missing. Set up Apex first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\setup_world_engine.py
if errorlevel 1 (
  pause
  exit /b 1
)
pause
