# Apex Code IDE

This app pins Eclipse Theia 1.76.0 and integrates its Monaco workbench with
Apex Code sessions. It adds language services, Run and Debug, tasks, an
interactive terminal, Git source control, settings, keybindings and VS Code compatible extensions
from Open VSX. It opens the session's Git working copy. Keep, Undo, checks,
agent conversations and review remain in Apex.

## Setup on Windows

Merge/apply the Apex changes on your laptop first. Update the Python environment
with `requirements.txt`, install Node.js 22 or 24 LTS, then run
`Setup-Apex-Code-IDE.cmd` from the Apex folder. The setup installs the locked npm
dependencies, downloads and checks the SHA-256 of the language/debugger extensions,
and builds the workbench. Native compilation may require Visual Studio Build
Tools with **Desktop development with C++**. Git and Python are also required.
Close IDE sessions before updating the engine.

On Linux, run `python scripts/setup_code_ide.py` from the repository root.
The initial bundles cover Windows x64 and Linux x64. Downloads and generated
builds are excluded from Git; the patch contains their reproducible source and
lock files. The full engine is about 800 MB installed, so installation is a
separate step from Apex's small editor.

Open `/code`, choose a project and **Open workspace**, then **Full IDE**. The
engine asks whether you trust the working copy before running its code. Use
**Focus IDE** for the whole width, and **Show Apex panels** to return to review.
**Reconnect IDE** refreshes the private connection without discarding the
existing editor buffers. **Save and close IDE** releases its processes.

## Everyday work

- Ctrl+P opens files; Ctrl+Shift+P opens commands; Ctrl+Shift+F searches the project.
- Ctrl+Space requests completion; F12 goes to a definition; F2 renames a symbol.
- Ctrl+S saves the active editor. Apex makes an Undo checkpoint after the IDE
  becomes clean. Before an Apex action or leaving the session, it requests
  Save All and waits for confirmation and a checkpoint. Saving failures stop
  that action and preserve the open buffers.
- Use **Terminal → New Terminal** and the **Run** menu for tasks and debugging.
  Python selects the working copy's virtual environment, then the original
  project's environment, then Apex's Python. Change it with **Python: Select
  Interpreter** or `python.defaultInterpreterPath`. Automatic terminal activation
  is disabled because Apex already supplies that environment; delayed activation
  commands must not be sent into programs waiting for keyboard input.
- Extensions install from the Open VSX view or a local `.vsix`. Installed
  extensions persist across sessions. Open IDE windows may need a reload after
  extension changes. The bundle includes Pyright, Python, debugpy, JavaScript /
  TypeScript language features, js-debug, Git, and HTML/CSS/JSON language support.

For a Python debug configuration, put this in `.vscode/launch.json` in the
working copy and select it in Run and Debug:

```json
{
  "version": "0.2.0",
  "configurations": [
    {"name": "Python file", "type": "debugpy", "request": "launch",
     "program": "${file}", "console": "integratedTerminal", "justMyCode": true},
    {"name": "Node file", "type": "pwa-node", "request": "launch",
     "program": "${file}", "console": "integratedTerminal"}
  ]
}
```

Click the gutter for a breakpoint, press F5, then inspect Variables, Watch and
Call Stack. F10 steps over, F11 steps into, and Shift+F5 stops. Project build
tools and compilers still need to be installed in the project's environment.

## Access and lifecycle

Only the master dashboard token can launch, checkpoint or close an IDE.
An HttpOnly, SameSite Strict cookie grants access to one IDE session for eight
hours; it grants no other Apex API access. Changing the master token, stopping
the runtime or restarting it invalidates the grant. Open sockets check
revocation too. Apex forwards a separate private credential to a Node server
bound to 127.0.0.1, and never forwards the dashboard token.

Extension webviews use a separate, authenticated loopback port with only webview
routes. They cannot read Apex's local storage or call its API through that
origin. Consequently webviews currently require a browser on the laptop using
HTTP. Remote clients and HTTPS reverse proxies can use editing and debugging,
but need a separately configured isolated webview origin for webview extensions.
Two IDE runtimes can be open at once. Keep and Throw away stop the runtime,
webview server, debuggers and terminal children before removing its working copy.
If Apex exits, the Node process also monitors its parent and exits.

The full IDE and extensions run as the laptop owner, like a desktop IDE. Code
Safe mode restricts the AI agent's commands; it does not sandbox commands you
run yourself in the IDE. Install extensions you trust. Open VSX compatibility
is individual: this does not promise every Microsoft Marketplace extension,
all VS Code proposed APIs, or a bundled compiler for every language.

## Verification and licenses

`tests/test_code_ide.py` covers access, proxy isolation, cookie revocation,
checkpoints, archive traversal and cleanup. With the engine installed, run:

```
APEX_IDE_INTEGRATION=1 python -m pytest -q tests/test_code_ide.py
```

On PowerShell, set `$env:APEX_IDE_INTEGRATION = '1'`, then run the pytest
command above. For the real browser acceptance, install `playwright@1.64.0`
in a separate npm folder, set `NODE_PATH` to its `node_modules`, run Playwright's
`install chromium`, then run `python scripts/check_code_ide_runtime.py` with
`--output-dir <results-folder>` from Apex's root. The CI job shows the exact
commands. The harness creates a scratch project, starts Apex and closes its
IDE and child processes when finished; it uses no model account.

The browser acceptance harness also tests real language services, breakpoint
variables, stepping, project search, interactive input, tasks, Git status,
extension installation, isolated webviews and saving.
See `docs/CODE-WINDOWS-ACCEPTANCE.md` for laptop validation and an actual agent
quality task. IDE tests do not establish the coding model's generation quality.

Apex's bridge and installer are MIT licensed. Eclipse Theia is EPL-2.0 with its
stated secondary licensing option; its packages retain their copyright and
license files. The bundled extensions retain their licenses in their unpacked
folders. `plugins.lock.json` records their exact provenance and checksums.
The extractor adapter replaces the vulnerable `decompress` package with
`@xhmikosr/decompress` 11.1.4 while preserving Theia's CommonJS interface.
