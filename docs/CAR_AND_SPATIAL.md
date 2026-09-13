# Car browser and spatial workspace

These are three views of the same Apex host, provider configuration, tools,
memory and permissions:

| View | Open on the dashboard host | Purpose |
| --- | --- | --- |
| Screen companion | `/companion` | Shared screen snapshots, conversation, evidence, floating panel |
| Car companion | `/drive` | Large controls, text/voice, reconnectable laptop tasks and saved results |
| Spatial workspace | `/board` | Host camera hand tracking, whole-object selection, view transforms, voice panel and manufacturing downloads |

## Car setup

Start Apex normally on the laptop, then open `/drive` on its dashboard URL.
Use the existing dashboard token. The car must be able to reach that URL over
HTTPS; opening localhost in the car points at the car, not the laptop. A private
VPN-only URL works only when the device/network actually has access to that VPN.
This change does not configure the car network, publish the laptop, or deploy an
always-on relay. Keep the Apex host running and awake. Use one dashboard process;
multiple Uvicorn workers sharing the database are not supported.

Use the interface while parked. This is a browser interface, not a native
CarPlay/Android Auto app and not a vehicle-control integration. The page checks
whether the browser exposes microphone recording and speech APIs. An available
API still needs permission and may fail on particular hardware. If the car does
not offer microphone access, use text or open the same `/drive` URL on a phone.
Recent tasks on either device retrieve the same host's tasks and conversation.
Phone microphone use is independent; the phone is not paired as a live audio
source for the car screen.

Discuss uses the companion's enforced read-tool boundary. Switch to Work to ask
for laptop work, for example: “Run the project's checks and tell me what failed,
with the test output.” Actual execution still depends on the host's tools,
credentials and existing safety gates. Voice uses tap-to-record transcription
and speaks completed replies; it is not full-duplex streaming audio. The existing
API/provider requirements and costs in [SCREEN_COMPANION.md](SCREEN_COMPANION.md)
also apply here.

### Disconnects and interruption

The server accepts a task with an idempotency ID before responding. Closing the
car tab or losing its connection does not cancel accepted work. The browser
remembers an uncertain submission and **Reconnect to task** uses the same ID,
so a lost response cannot turn a retry into a second shell command. On another
device, choose the task under **Recent tasks**. Tool evidence and final results
are saved on the host; up to 100 recent evidence events and 200,000 response
characters are retained per task. Recent tasks lists the latest 30; older IDs
remain retrievable. Records currently have no automatic retention cleanup.

**Stop** requests cancellation between tool calls. It cannot undo work already
completed or forcibly terminate every executing tool. A host crash/restart marks
unfinished jobs interrupted on the next access; it does not replay them. Inspect
the filesystem/tool evidence before issuing a replacement action. No result is
reported as completed solely because a browser disconnected.

Car turns and screen-companion turns share the same `companion:<thread_id>`
channel and busy guard. A job can be resumed from a different authenticated
browser on the same host. Dashboard tokens remain trusted operator credentials;
this is not multi-user tenant isolation. Per-device token issuance/revocation
uses the existing dashboard controls.

## Voice and hands

Enable `HANDTRACK_ENABLED=true` and `BOARD_ENABLED=true` in your existing Apex
configuration, ensure the optional MediaPipe/camera dependencies are installed,
and restart Apex. Open `/board`. Tracking runs on the Apex host's camera, not the
car/phone/browser camera. Touch selection works even when tracking is off.

1. Click **Talk to Apex**. The companion panel opens within the board.
2. In Work mode ask Apex to create a measured model using the existing Blender
   tools, or load an existing GLB from the props folder. Blender creation still
   requires the configured Blender bridge. Prefer self-contained GLB files.
3. Pinch and hold over a model to grab it; use two hands to scale/rotate its view.
   The selection label remains after release. You can also tap an object's label
   to select it. An open palm cancels an active hand transform as before.
4. Release it and say “Rotate this by a quarter turn” or “Make this twice as
   large on the board.” A selection snapshot, including the exact object ID and
   model source, accompanies the message. `board_transform` supports position,
   display scale and Y rotation; these operations participate in undo/redo.
5. Say “Undo that.” The existing board undo tool reverses the view change.

Selection identifies a **whole object**, not a mesh face, eye, finger or other
subpart. Display scale is not physical size. Use explicit millimetre dimensions
with the existing design tools when creating physical geometry. Selection is
retained while the host process runs; it must be chosen again after restart.
Stale IDs and transforms of an object currently held by a hand are refused.

Model requests carry dashboard authentication. Referenced model resources must
remain inside the props jail; external resource URLs are refused. Reloading a
new asset version replaces the visible model and discards obsolete responses.

## From a selected model to a manufacturing file

Ask Apex to check the selected model with Forge and explain its report. The
selection context instructs it to use the selected card's immutable `src` path,
so viewing v1 while v2 exists does not silently export v2. Export of a registered
asset path records the exact source version as its parent. Unregistered files
can be checked but must be registered through the design tools before export.

Request a 3MF or STL export when ready. On the board, click **Refresh exports**
and select the file to download it with authentication. The original visual
model remains available; restoring an asset whose newest version is a
manufacturing file picks its latest visual version, or explains that none exists.

Open the downloaded file in the printer's slicer to choose the machine, material,
supports and process settings. Forge's existing checks use the configured
manufacturing assumptions, including nozzle/build volume; they do not constitute
resin-printer validation. This change does not send print jobs, control a Formlabs
printer, or establish that a part has physically printed. Configure and validate
the actual manufacturing process separately.

## Verification

`tests/test_drive_spatial.py` exercises actual HTTP routes with a deterministic
fake agent: disconnect-independent work, duplicate submissions, cancellation,
shared busy guards, restart recovery, selection identity, undo/redo, and jailed
downloads. `tests/test_forge_tool.py` checks exact-version export geometry and
lineage. Existing companion, board, Forge, and repository regression tests apply.

`scripts/check_companion_ui.cjs` and `scripts/check_drive_spatial_ui.cjs` simulate
DOM, provider/media APIs, networking and Three.js. They cover recovery after a
lost acceptance response, selected-object controls, the voice panel, auth headers,
resource containment, obsolete loads and version refresh. Run with `jsdom` on
Node's module path. These are not real GPU rendering or hardware media tests.

Before calling the hardware setup verified, check on the actual devices:

- The parked car can reach the authenticated HTTPS dashboard and play a reply.
- Microphone capture/transcription works, or the chosen phone/text fallback works.
- A harmless laptop test continues while the car tab closes, and its result can
  be reopened without a second execution.
- The laptop camera can grab/release/select a model; spoken “this” targets that
  model; view undo/redo visibly works.
- A saved GLB version refreshes in the real browser, and an exported file opens
  in the intended slicer at the expected millimetre dimensions.

Reference API behavior: [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
and [Three.js LoadingManager](https://threejs.org/docs/pages/LoadingManager.html).
