@echo off
REM Let Apex's Work tasks run on your Claude and ChatGPT plans instead of API credits.
REM Installs Claude Code and OpenAI Codex (the official apps), opens each so you can sign in
REM with your own account in the browser (Apex never sees your password), then checks both
REM with one tiny real task each. See docs\WORK.md, "Always on, on your plans".
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"

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
where git >nul 2>nul || echo Note: if Claude Code asks for Git for Windows, install it from https://git-scm.com and run this again.
echo When Claude Code opens: choose your Claude account (Pro or Max), NOT an API key or Console account.
echo Once you see its prompt, type /exit and press Enter.
pause
call claude

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
echo Codex will now open a browser page: choose "Sign in with ChatGPT".
pause
call codex login

echo.
echo [3/3] Checking both plans with one tiny real task each (a few seconds of your usage)
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" set "APEX_PYTHON=python"
"%APEX_PYTHON%" scripts\work_plans_check.py --live
echo.
echo If both say READY: restart Apex, open Work, then Always on, and switch it on.
echo If one says NOT READY, the line above it says what to do. Send it to Apex's maintainer if unsure.
pause
