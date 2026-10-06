# Start Apex and Celine together

For the prepared platform (Apex, Celine, Apocalypse and cached NOMAD), open Docker Desktop first, stop earlier Apex launcher windows with Ctrl+C, then run:

```cmd
cd /d C:\Users\alexk\Apex
set "APEX_APOCALYPSE_HOME=D:\Apex-Apocalypse"
Start-Apex-Platform.cmd
```

This opens Apex Home and Apocalypse after checking their service identities and agent readiness. Celine still needs browser Voice mode for microphone/playback. NOMAD is optional: the launcher checks Linux Docker and cached images, then uses `--pull never --no-build`. Missing images are skipped, with no model, content, reader-image or map downloads. The large library remains deferred. A ready dashboard is not proof that every app is authorized or every downloaded archive has a reader.

Without internet, use `Start-Apex-Platform.cmd --offline-only`. Add `--no-nomad` to skip Docker. This starts the offline dashboard only; normal cloud Apex and Celine are omitted. The existing launchers below still work independently.

Keep the platform console open. Ctrl+C requests a graceful stop of the Apex processes it created; a failed service stops its companion Apex processes. Previously running services are never killed. NOMAD containers remain managed by Docker Desktop after the launcher closes, preserving database and library volumes.

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
