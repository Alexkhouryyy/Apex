@echo off
setlocal
REM Downloads the Apocalypse library and keeps going until it is all there.
REM Internet drops: it waits and retries on its own. Window closed or PC restarted:
REM run this again and it continues from where it stopped (finished archives are
REM skipped, half-downloaded ones resume). Ctrl+C stops safely.
REM   Download-Apex-Library.cmd 90      download everything that fits in 90 GB
cd /d "%~dp0"
if not defined APEX_APOCALYPSE_HOME set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
if not exist ".venv\Scripts\python.exe" (
  echo Apex's Python environment is missing. Run install-apex.bat first.
  pause
  exit /b 1
)
set "LIMIT=%~1"
if "%LIMIT%"=="" (
  echo How much of the library to download, in GB, counting what you already have:
  echo   90   books, guides, medicine, TED talks
  echo   220  the above plus full English Wikipedia with images
  echo   360  everything, including the worldwide map
  echo Your drive needs that much free space plus 20 GB. Library folder: %APEX_APOCALYPSE_HOME%
  set /p "LIMIT=GB: "
)
if "%LIMIT%"=="" (
  echo No size given. Nothing was downloaded.
  pause
  exit /b 1
)
echo Downloading up to %LIMIT% GB into %APEX_APOCALYPSE_HOME%. Keep this window open.
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py --max-download-gb %LIMIT% --until-done
if errorlevel 1 (
  echo Stopped. Every finished archive and partial file was kept: run this again to continue.
  pause
  exit /b 1
)
echo Done. Every archive up to %LIMIT% GB is downloaded and checksum-verified.
pause
exit /b 0
