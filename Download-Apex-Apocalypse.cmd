@echo off
setlocal
cd /d "%~dp0"
if not defined APEX_APOCALYPSE_HOME set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
echo Preparing the local AI model. Large files stay in %APEX_APOCALYPSE_HOME%.
".venv\Scripts\python.exe" -X utf8 scripts\setup_apex_apocalypse.py
if errorlevel 1 goto stopped
echo Preparing English knowledge and worldwide maps. Keep this window open.
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py
if errorlevel 1 goto stopped
echo Selected archives downloaded. NOMAD readers, courses and remaining setup still need preparation.
pause
exit /b 0
:stopped
echo Preparation stopped. Existing files were kept. Rerun this command to resume archive downloads.
pause
exit /b 1
