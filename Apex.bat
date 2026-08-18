@echo off
setlocal enabledelayedexpansion
title Apex
cd /d "%~dp0"

echo.
echo   ===========================================
echo     A P E X
echo   ===========================================
echo.

REM ---- 1. Locate Python, auto-install if missing ----
set "PYCMD="
python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    py --version >nul 2>&1 && set "PYCMD=py"
)
if not defined PYCMD (
    echo   [setup] Python is not installed - setting it up for you...
    winget --version >nul 2>&1
    if errorlevel 1 (
        echo   [X] Automatic install needs 'winget', which this PC lacks.
        echo       Install Python 3.10+ from https://www.python.org/downloads/
        echo       Tick "Add python.exe to PATH", then run Apex.bat again.
        echo.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo   [setup] Python installed. Windows needs a fresh window to see it.
    echo           Close this window and double-click Apex.bat once more.
    echo.
    pause
    exit /b 0
)

REM ---- 2. Create the virtual environment, first run only ----
if not exist ".venv\Scripts\python.exe" (
    echo   [setup] Creating virtual environment...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo   [X] Could not create the virtual environment.
        echo.
        pause
        exit /b 1
    )
)
set "VPY=.venv\Scripts\python.exe"

REM ---- 3. Install dependencies, first run only ----
if not exist ".venv\.apex_ready" (
    echo   [setup] Installing dependencies.
    echo           One-time step - can take 10-20 minutes, ~2 GB download.
    echo.
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   [X] Dependency install failed - see the errors above.
        echo.
        pause
        exit /b 1
    )
    echo   [setup] Downloading optional browser engine for web tools...
    "%VPY%" -m playwright install chromium
    echo ready>".venv\.apex_ready"
    echo.
    echo   [setup] Setup complete - future launches will be instant.
    echo.
)

REM ---- 4. Make sure an Anthropic API key is configured ----
set "NEEDKEY="
if not exist ".env" set "NEEDKEY=1"
if exist ".env" (
    findstr /c:"your_key_here" ".env" >nul 2>&1 && set "NEEDKEY=1"
    findstr /b /c:"ANTHROPIC_API_KEY=" ".env" >nul 2>&1 || set "NEEDKEY=1"
)
if defined NEEDKEY (
    echo   Apex needs your Anthropic API key. This is asked once.
    echo   Get one at https://console.anthropic.com/settings/keys
    echo.
    set /p "APIKEY=  Paste your key here and press Enter: "
    REM Set just this one line. This used to be `>".env" echo KEY=value`, and a
    REM single `>` truncates the file — anyone with a leftover placeholder on
    REM another line lost DASHBOARD_TOKEN, VAPID_PRIVATE_KEY and their channel
    REM tokens. The VAPID private key cannot be regenerated without breaking
    REM every existing push subscription.
    "%VPY%" scripts\set_env_key.py ANTHROPIC_API_KEY "!APIKEY!"
    if errorlevel 1 (
        echo   [X] Could not write .env - check the errors above.
        echo.
        pause
        exit /b 1
    )
    echo.
)

REM ---- 5. Launch Apex ----
echo   Starting Apex. Type 'quit' to exit.
echo.
"%VPY%" main.py --tui

echo.
echo   Apex has stopped.
pause
