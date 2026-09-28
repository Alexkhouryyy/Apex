@echo off
REM One console owns Apex resident mode and Celine's local voice server.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\run_apex_qwen.py --fast --resident --open
set "APEX_EXIT=%ERRORLEVEL%"
pause
exit /b %APEX_EXIT%
