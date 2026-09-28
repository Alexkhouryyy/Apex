# Start Apex and Celine together

In Command Prompt:

```cmd
cd /d C:\Users\alexk\Apex
Start-Apex-All.cmd
```

First stop any older launcher with Ctrl+C, or quit resident Apex from its tray.
The new launcher refuses to take over occupied ports or stop unrelated servers.

This starts the fast local Qwen Celine voice service, then Apex resident mode:
the dashboard (Home, Apps, Screen Companion and Spatial Workspace), tray,
hotkeys, scheduler, memory, enabled awareness features and configured MCP
discovery. Existing feature switches remain in effect. Celine wake listening
defaults on unless you explicitly disabled it in `.env` or the environment.
Resident mode shares one listener for Apex and Celine wake phrases.

The launcher waits for the voice model and the dashboard's health response
from the newly started Apex process before reporting them ready. Other
services still depend on their own settings, credentials and hardware; their
startup diagnostics are in `%USERPROFILE%\.apex\resident.log` (or your configured
`RESIDENT_LOG_FILE`). A healthy dashboard does not certify every integration.

On Windows, a unique launch identifier links the health response to this start;
the Python virtual environment's launcher can have a different process ID from
the actual interpreter. Shutdown stops the owned process trees, including those
interpreter children. If an older launcher timed out but Apex remains running,
quit that instance from its tray before starting again.

It opens Screen Companion after the dashboard responds. Click Voice and allow
microphone/audio access in the browser when prompted. Keep Companion open for
Celine's screen assistance. GPU warm-up can take several minutes on first start.

Keep the launcher window open. Ctrl+C stops its Apex and voice processes.
The dashboard's Restart button restarts Apex while retaining the loaded voice
model; crashes stop the launcher instead of creating an endless restart loop.

App connections still need their keys and account authorization in Apps.
Composio setup is separate; this launcher does not authorize accounts or bypass
model provider credits. The local voice environment must already be installed
with `Test-Apex-Fast-Voice.cmd`, and Apex dependencies with `Apex.bat`.
