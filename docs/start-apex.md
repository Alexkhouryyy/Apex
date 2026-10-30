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

This starts Apex resident mode right away (the dashboard with Home, Apps,
Screen Companion and Spatial Workspace, tray, hotkeys, scheduler, memory,
enabled awareness features and configured MCP discovery) and warms the fast
local Qwen Celine voice in the background.

**One failure doesn't stop everything.** Apex never waits for Celine's voice.
If the voice model can't load (for example the GPU is out of memory or Windows
reports a paging-file error), or the voice environment isn't installed, the
console says so and Apex keeps running: replies are spoken with the PC's own
voice until Celine's server answers. `Start-Apex-Platform.cmd` treats Apex and
the offline Apocalypse service the same way: if one fails to start or stops,
the other keeps running.

**Every launcher has the same defaults.** They live in `config.py`, which every
entry point reads: `Start-Apex-All.cmd`, the sign-in launcher, `start-apex.bat`,
`Start-Apex-Platform.cmd` and plain `main.py` all start with the desktop
Companion on and no wake phrase listening (hotkeys F8 and Ctrl+Alt+C start
requests). No launcher injects settings into the environment, so a value in
`.env` always wins, also after a dashboard Restart. For example,
`CELINE_WAKE_ENABLED=true` turns on "Hey Celly". Hand tracking
(`HANDTRACK_ENABLED=true`) no longer depends on `AWARENESS_ENABLED`.

**Speech never goes silent.** When Celine's voice server is starting, busy,
hung or stopped, replies are spoken in the PC's own voice (barge-in and the stop
gesture still work), with a 30-second cool-down before Celine is asked again. A
sentence is never said twice, and replies longer than 4,000 characters are split
so Celine can read them.

**Celine knows what is running.** Every turn, her prompt lists which controls
are live (desktop Companion and its hotkeys, hand tracking, her voice state,
wake phrases, awareness), so she doesn't deny a control that exists, and she
says how to turn on one that's off.

The launcher waits for the dashboard's health response from the newly started
Apex process before reporting it ready, and announces "Celine voice: ready."
when the voice model has loaded. Other services still depend on their own
settings, credentials and hardware; their startup diagnostics are in
`%USERPROFILE%\.apex\resident.log` (or your configured `RESIDENT_LOG_FILE`).
A healthy dashboard does not certify every integration.

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
