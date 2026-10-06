@echo off
REM Let Apex's Work tasks run on your Claude and ChatGPT plans instead of API credits.
REM Installs Claude Code and OpenAI Codex (the official apps), signs each in with your own
REM account in the browser (Apex never sees your password), then checks both with one tiny
REM real task each. A plan that is already signed in correctly is skipped.
REM See docs\WORK.md, "Always on, on your plans".
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" set "APEX_PYTHON=python"

echo.
echo [1/3] Claude Code, for your Claude Pro or Max plan
where claude >nul 2>nul
if errorlevel 1 (
  echo Installing Claude Code with Anthropic's installer...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
)
where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code did not install. See https://code.claude.com/docs and run this again.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\work_plans_check.py --signed-in claude
if errorlevel 1 (
  echo A browser page opens: sign in with your Claude account. This uses your subscription, never API billing.
  call claude auth logout >nul 2>nul
  call claude auth login --claudeai
)

echo.
echo [2/3] Codex, for your ChatGPT plan
where codex >nul 2>nul
if errorlevel 1 (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo Codex needs Node.js. Install the LTS version from https://nodejs.org, then run this again.
    pause
    exit /b 1
  )
  call npm install -g @openai/codex
)
"%APEX_PYTHON%" scripts\work_plans_check.py --signed-in chatgpt
if errorlevel 1 (
  echo A browser page opens: choose "Sign in with ChatGPT".
  call codex logout >nul 2>nul
  call codex login
)

echo.
echo [3/3] Checking both plans with one tiny real task each (a few seconds of your usage)
"%APEX_PYTHON%" scripts\work_plans_check.py --live
echo.
echo If both say READY: restart Apex, open Work, then Always on, and switch it on.
echo If one says NOT READY, the line above it says what to do. Paste it to Claude if unsure.
pause
