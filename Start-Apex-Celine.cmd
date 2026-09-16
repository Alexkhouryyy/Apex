@echo off
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" set "APEX_PYTHON=%USERPROFILE%\Apex.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Restore the Apex virtual environment first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\run_apex_qwen.py
pause
