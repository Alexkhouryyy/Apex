@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -X utf8 scripts\run_apex_apocalypse.py %*
set "APEX_APOCALYPSE_EXIT=%ERRORLEVEL%"
pause
exit /b %APEX_APOCALYPSE_EXIT%
