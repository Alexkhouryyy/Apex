@echo off
setlocal
cd /d "%~dp0.."
if not defined APEX_TEST_PYTHON (
  if exist ".venv\Scripts\python.exe" set "APEX_TEST_PYTHON=%CD%\.venv\Scripts\python.exe"
)
if not defined APEX_TEST_PYTHON (
  if exist "venv\Scripts\python.exe" set "APEX_TEST_PYTHON=%CD%\venv\Scripts\python.exe"
)
if not defined APEX_TEST_PYTHON (
  if exist "..\Apex\.venv\Scripts\python.exe" set "APEX_TEST_PYTHON=%CD%\..\Apex\.venv\Scripts\python.exe"
)
if not defined APEX_TEST_PYTHON (
  if exist "..\Apex\venv\Scripts\python.exe" set "APEX_TEST_PYTHON=%CD%\..\Apex\venv\Scripts\python.exe"
)
if not defined APEX_TEST_PYTHON set "APEX_TEST_PYTHON=python"
if not exist ".env" (
  if exist "..\Apex\.env" (
    "%APEX_TEST_PYTHON%" -c "from dotenv import load_dotenv; load_dotenv(r'..\Apex\.env'); import runpy,sys; sys.argv=['main.py','--text']; runpy.run_path('main.py',run_name='__main__')"
    goto finished
  )
)
"%APEX_TEST_PYTHON%" main.py --text
:finished
if errorlevel 1 (
  echo Apex did not start successfully. Keep this output for diagnosis.
  pause
)
endlocal
