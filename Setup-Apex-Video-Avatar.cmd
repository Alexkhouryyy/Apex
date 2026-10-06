@echo off
REM Install the photoreal video avatar (MuseTalk) on this PC. See docs\VIDEO_AVATAR.md.
REM About 10 GB and 20-40 minutes. Run it again to finish an interrupted install.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\setup_video_avatar.py %*
pause
