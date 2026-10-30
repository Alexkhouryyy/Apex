@echo off
setlocal
REM Measures the hardware Apex depends on: webcam and hand detection, microphone,
REM speakers, GPU memory for Celine's voice, paging file, disks, displays, screen
REM capture and both voices. Saves a dated report and shows what changed since the
REM last one. Changes nothing.
REM   Test-Apex-Hardware.cmd          the full check, with a short hands-on demo
REM   Test-Apex-Hardware.cmd quick    just measure, nothing to do
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
if /i "%~1"=="quick" (
  ".venv\Scripts\python.exe" -X utf8 -m tools.hardware_check
) else (
  echo For the camera check, quit Apex first: its hand tracker holds the webcam.
  ".venv\Scripts\python.exe" -X utf8 -m tools.hardware_check --demo
)
pause
