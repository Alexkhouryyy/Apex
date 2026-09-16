@echo off
cd /d "%~dp0"
set "QWEN_PYTHON=%USERPROFILE%\apex-qwen-env\Scripts\python.exe"
if not exist "%QWEN_PYTHON%" (
  echo Existing apex-qwen-env Python was not found.
  pause
  exit /b 1
)
"%QWEN_PYTHON%" scripts\test_fast_qwen.py
pause
