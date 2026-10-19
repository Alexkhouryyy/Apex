@echo off
setlocal
REM Downloads the Apocalypse library and keeps going until it is all there.
REM Internet drops: it waits and retries on its own. Window closed or PC restarted:
REM it continues by itself at the next sign-in, or run this again (finished archives
REM are skipped, half-downloaded ones resume). Ctrl+C stops safely.
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
REM After a restart or power cut, Windows runs this again when you sign in, until
REM the download is finished; then the sign-in entry removes itself.
set "RESUME=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Apex-Library-Resume.cmd"
(
  echo @echo off
  echo REM Apex: continue the library download after a restart. Deleted when it finishes.
  echo call "%~f0" %LIMIT%
) > "%RESUME%"
echo Downloading up to %LIMIT% GB into %APEX_APOCALYPSE_HOME%. Keep this window open.
echo If the PC restarts or the power goes, it carries on by itself when you sign in.
".venv\Scripts\python.exe" -X utf8 scripts\prepare_apocalypse_library.py --max-download-gb %LIMIT% --until-done
if errorlevel 1 (
  echo Stopped. Every finished archive and partial file was kept: run this again to continue.
  echo It will also try again when you next sign in. To stop that, delete:
  echo   %RESUME%
  pause
  exit /b 1
)
if exist "%RESUME%" del "%RESUME%"
echo Done. Every archive up to %LIMIT% GB is downloaded and checksum-verified.
pause
exit /b 0
