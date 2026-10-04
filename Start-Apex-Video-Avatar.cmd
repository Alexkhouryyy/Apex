@echo off
REM Start the photoreal video avatar beside Apex. Needs Setup-Apex-Video-Avatar.cmd once,
REM and the idle video at %USERPROFILE%\apex-video-avatar\apex-idle.mp4 (docs\VIDEO_AVATAR.md).
REM The first start prepares the face (a few minutes); later starts reuse it.
cd /d "%~dp0"
set "AVATAR_PYTHON=%USERPROFILE%\apex-video-avatar\env\Scripts\python.exe"
if not exist "%AVATAR_PYTHON%" (
  echo The video avatar is not installed. Run Setup-Apex-Video-Avatar.cmd first.
  pause
  exit /b 1
)
"%AVATAR_PYTHON%" scripts\avatar_server.py %*
pause
