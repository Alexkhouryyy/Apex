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
set "APEX_TEST_ENV_FILE=%CD%\.env"
if not exist ".env" (
  if exist "..\Apex\.env" set "APEX_TEST_ENV_FILE=%CD%\..\Apex\.env"
)
rem Load exactly one Apex file, then prevent main/config from searching parents.
"%APEX_TEST_PYTHON%" -c "import os,runpy,sys; from dotenv import load_dotenv; load_dotenv(os.environ['APEX_TEST_ENV_FILE'],encoding='utf-8-sig'); os.environ['PYTHON_DOTENV_DISABLED']='1'; sys.argv=['main.py','--text']; runpy.run_path('main.py',run_name='__main__')"
:finished
if errorlevel 1 (
  echo Apex did not start successfully. Keep this output for diagnosis.
  pause
)
endlocal
