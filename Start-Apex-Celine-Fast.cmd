@echo off
REM Celine, streamed: the fast Qwen engine. Needs Test-Apex-Fast-Voice.cmd run once.
REM The first start after a reboot warms the GPU up for a minute or two.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\run_apex_qwen.py --fast
pause
