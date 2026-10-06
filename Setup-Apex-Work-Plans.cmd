@echo off
REM Let Apex's Work tasks run on your Claude and ChatGPT plans instead of API credits.
REM Installs Claude Code and OpenAI Codex (the official command-line apps) and opens each once so
REM you can sign in with your own account in the browser. Apex never sees your password.
REM See docs\WORK.md, "Always on, on your plans".
cd /d "%~dp0"
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js is needed first. Install the LTS version from https://nodejs.org, then run this again.
  pause
  exit /b 1
)
echo.
echo [1/2] Claude Code, for your Claude Pro or Max plan
where claude >nul 2>nul || call npm install -g @anthropic-ai/claude-code
echo When Claude Code opens, choose your Claude account (not an API key), then type /exit.
pause
call claude
echo.
echo [2/2] Codex, for your ChatGPT plan
where codex >nul 2>nul || call npm install -g @openai/codex
echo When Codex opens, choose "Sign in with ChatGPT", then press Ctrl+C twice to leave.
pause
call codex
echo.
echo Done. Restart Apex, open Work, then Always on: both plans should say "ready".
pause
