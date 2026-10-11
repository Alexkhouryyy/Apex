# Running Apex tests on your laptop

Use the existing Python environment and the isolated launcher. On PowerShell:

```powershell
Set-Location "$env:USERPROFILE\Apex"
.\.venv\Scripts\python.exe -X utf8 scripts\run_tests.py
```

The launcher uses UTF-8, disables the pytest cache, and creates a new temporary
folder for every run. On Windows it lives in `%LOCALAPPDATA%\ApexTestRuns` rather
than the shared `Temp\pytest-of-<user>` folder. It does not load your laptop's
`.env`. Model settings use the shipped defaults, and the database, vault, study
files and sandbox point into the test run's folder. These are subprocess
settings; your running Apex and `.env` are unchanged.

Pass pytest selections and options after the script name. For the failures
reported by the broad Windows run, start with:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\run_tests.py tests/test_answers.py tests/test_autonomy.py tests/test_boot_stdin.py tests/test_command_layout.py tests/test_doctor.py tests/test_live_avatar.py tests/test_model_capabilities.py tests/test_relay_answer.py tests/test_research_boundaries.py tests/test_sandbox.py tests/test_smoke.py tests/test_wiring.py tests/test_prefs.py tests/test_test_runner.py
```

The optional Chronicle replay tests require `requirements-research.txt`.
Without it, those tests report skipped. IDE integration and real coding-plan
acceptance have their own setup in `CODE-WINDOWS-ACCEPTANCE.md`; unit test results
do not establish the quality of your connected coding agent.

Keep the run folder when investigating failures. Pytest owns only the fresh
`pytest` subfolder selected for that invocation, so it cannot clear an older
test run or project directory through this launcher.
