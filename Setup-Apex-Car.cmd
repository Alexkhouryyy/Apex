@echo off
REM Open Apex in the car: a private HTTPS address on your Tailscale network.
REM See docs\CAR_AND_SPATIAL.md. "Setup-Apex-Car.cmd --off" turns it off.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\car_setup.py %*
pause
