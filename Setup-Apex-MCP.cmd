@echo off
REM Share Apex's memory and skills with Claude Code, Codex, Claude Desktop or Cursor.
REM Prints the exact setup for each, with this computer's paths. See docs\MCP_SERVER.md.
cd /d "%~dp0"
set "APEX_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%APEX_PYTHON%" (
  echo Apex Python was not found. Run Apex.bat once first.
  pause
  exit /b 1
)
"%APEX_PYTHON%" scripts\apex_mcp.py --print-config
echo.
where claude >nul 2>nul && (
  set /p ADD="Add Apex to Claude Code now? [y/N] "
  call :maybe_add
)
pause
exit /b 0

:maybe_add
if /i "%ADD%"=="y" claude mcp add --scope user apex -- "%APEX_PYTHON%" "%~dp0scripts\apex_mcp.py"
exit /b 0
