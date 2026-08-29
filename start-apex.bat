@echo off
REM ============================================================
REM  Apex Resident - quick launcher
REM  Double-click to start Apex in the background (no console).
REM  Use this if autostart didn't fire, or after you quit Apex.
REM ============================================================

cd /d "%~dp0"
REM Launched through the supervisor rather than main.py directly, so the
REM dashboard's Restart button has something to bring Apex back. The supervisor
REM restarts ONLY on exit code 42 - a crash is not a restart request, and
REM respawning one would loop forever while looking like Apex is running.
start "" pyw -3 scripts\apex_supervisor.py --resident
exit /b 0
