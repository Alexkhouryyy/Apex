@echo off
REM Install the browser speech model for hands-free voice (about 14 MB, pinned and hash-checked).
REM See docs\HANDS_FREE_COMPANION.md. "Setup-Apex-Speech-Model.cmd --check" only checks it.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\fetch_speech_model.py %*
pause
