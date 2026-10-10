# Check Apex Code on Windows

Remember to merge/apply these changes on your Windows laptop. After merging an Apex update, install its updated requirements
and restart Apex. Use the Python environment selected by your launcher. For
the usual environment in the repository, run from PowerShell in the Apex folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q tests/test_code_terminal.py tests/test_code_workspace.py
```

These tests use scratch projects and real local shells. They do not call a
model. Windows requires Git and `pywinpty`; the dependency list selects the
Windows terminal package automatically. A Linux pass does not substitute for
this Windows check. CI also runs this check on `windows-latest`.

For Full IDE, install Node.js 22 or 24 LTS and run
`Setup-Apex-Code-IDE.cmd`. Native builds can need Visual Studio Build Tools
with Desktop development with C++. The `code-ide` CI job builds and tests the
actual engine on Windows and Linux. Its successful Windows Node 24 artifact
can alternatively be extracted into the repository's `ide` folder on a laptop
using Node 24, so the native build need not be repeated. It contains generated
runtime files, not your project or credentials.

After installing the IDE, also run its real access and lifecycle checks:

```powershell
$env:APEX_IDE_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_code_ide.py
```

Choose Full IDE in a scratch workspace and trust that working copy. Check
Ctrl+Space, F12, F2 and the Problems panel on Python and TypeScript code. Add
the debug configurations from [the IDE guide](../ide/README.md), set a
breakpoint, press F5, inspect Variables/Watch and step with F10. Check both
Python and Node. Install and uninstall an Open VSX extension and a local VSIX.
Run a configured task and confirm Git detects a changed file in Source Control.
Check an extension webview from the laptop's HTTP window. Finally edit a file
without pressing Ctrl+S, trigger an Apex action, and verify Save All succeeds
before it proceeds. Use Save and close IDE and confirm debug targets and
terminal jobs stop. Existing browser buffers must survive switching views.

1. In `/setup`, enter your master dashboard token. If it is missing or rejected,
   use the visible Windows configuration steps, restart Apex and refresh.
2. In `/code`, add a small Git project, or create a new project in a new folder
   under an existing parent. Choose **Open workspace**. This must work before
   connecting Claude or ChatGPT.
3. Open a file, change it, use Ctrl+S and confirm the saved version. Create
   `src/check.py` with `print('Apex editor works')`, open a second file, switch
   tabs and undo an edit. Search for `Apex editor works` with Ctrl+Shift+F.
4. Open the terminal with Ctrl+`. Run `python src/check.py`. Then run:

   ```text
   python -u -c "from pathlib import Path; print(Path.cwd()); print(input('Your name: '))"
   ```

   Type a name and press Enter. Run `cd src`, then `cd ..`. The directory must
   persist between commands. Also try a project path containing spaces.
5. Create `index.html` with a button that increments a number. Run
   `python -m http.server 8000` in the terminal, enter
   `http://127.0.0.1:8000` in Preview and click the button. Edit and save while
   the server runs. Close the shell and confirm the server stops.
6. Rename and delete the scratch Python file. Use Undo last step to restore it.
   Confirm the original project has not changed. Run your project's checks,
   review the changes, then Keep the workspace. Check the original project
   now contains those changes. Use Throw away on a second scratch session.
7. On your phone, open Code through your existing Apex connection. Open Files,
   select a file, edit and save, then return to the editor. Check labels and
   controls fit the screen. A preview URL on a phone uses the laptop's reachable
   network address rather than the phone's `127.0.0.1`.

For agent quality, connect a plan with `Setup-Apex-Work-Plans.cmd`, select it
and try this task in a separate scratch project:

> Build a local expense tracker using the project's existing stack. I need to
> add and delete expenses with a date, category and amount, see a total, export
> CSV, and keep entries after restarting the app. It must handle empty data and
> invalid amounts clearly. Complete the working app, run its checks, start it
> and give me the URL and exact verification results. Ask if an essential
> requirement is unclear.

Check each requirement yourself, including export and restart persistence.
Try switching agents after a follow-up and confirm it retains the original
requirements. Record the task, chosen model, missing behavior and command
output if it fails. Prompt and handoff fixes improve the supplied context;
they do not establish a model's generation quality without this real run.

Voice, camera, GPU features, paired-device permissions and remote access each
need their own device checks in [PROVE_IT.md](PROVE_IT.md). The automated
suite cannot establish microphone quality or how the laptop feels in use.
