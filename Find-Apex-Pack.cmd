@echo off
setlocal
REM Searches the Kiwix catalog for offline packs and lists their names and sizes.
REM Downloads nothing. A language word (arabic, french...) limits the language.
REM   Find-Apex-Pack.cmd arabic
REM   Find-Apex-Pack.cmd arabic medicine
REM   Find-Apex-Pack.cmd first aid
REM Then add one with Add-Apex-Pack.cmd NAME.
cd /d "%~dp0"
if not defined APEX_APOCALYPSE_HOME set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
set "WORDS=%*"
if "%WORDS%"=="" set /p "WORDS=What to look for, e.g. arabic medicine: "
if "%WORDS%"=="" exit /b 1
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py --find %WORDS%
set "RC=%errorlevel%"
pause
exit /b %RC%
