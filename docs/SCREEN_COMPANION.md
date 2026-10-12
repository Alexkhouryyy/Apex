# Apex screen companion

The companion brings conversation, a shared screen, evidence from tools, and
task execution into one compact view. It connects to the existing Apex agent;
it does not create a separate brain or replace the dashboard.

The integrated [car browser and spatial workspace](CAR_AND_SPATIAL.md) add
reconnectable host tasks, gesture-selected voice context and export downloads.

## Start on your laptop

### Windows: background startup and F8

Run `Setup-Apex-Screen-Companion.cmd` once from your Apex checkout. It installs
the optional pinned native window and registers **current-user login startup**;
administrator privileges and Visual Studio Build Tools are not required.
An existing Apex resident startup entry is backed up and restored on uninstall.
The setup does not edit your `.env` or download a new voice model.

- **F8** opens a compact, always-on-top Screen Companion and starts screen help.
  Press F8 again from any application to stop and hide it. A laptop with media
  keys may require Fn+F8; `COMPANION_HOTKEY=<f7>` in `.env`, for example, selects
  another key on the next Apex start. The older Celine/browser shortcuts remain
  available separately.
- The native window loads silently. It does not capture the screen or begin
  recording at login. The first voice activation asks for microphone permission.
  Windows microphone privacy settings still apply. The decision is stored in
  `%LOCALAPPDATA%\Apex\Companion\permissions.json`; removing that file lets you
  make the decision again.
- **Chat** expands the native window for typed questions and full history.
  When screen help is active, a fresh host snapshot accompanies each question,
  including typed questions in Chat. This is snapshot-based assistance, not a
  continuous video feed. F8, Stop, or Esc in the Companion ends it.
- When the prepared `apex-qwen-fast-env` exists, login starts the local Celine
  voice service in parallel with Apex. GPU warm-up does not hold back the
  dashboard or hotkey. Until local voice is available, native voice mode prefers
  the device voice and says so. Otherwise your existing voice settings apply.
- The resident tray includes **Screen Companion** for opening the full window
  without starting the microphone. **Quit Apex** shuts down its owned desktop
  window and drawing surface. Closing the native window stops/hides it while
  leaving Apex ready for the next hotkey.

The new native window runs on its own main GUI thread; the older optional orb
is not started alongside it. An already running, updated Apex can be attached
through its local authenticated desktop endpoint. Startup refuses unrelated
services on the dashboard port and does not kill or replace existing processes.
Normal Apex background features still follow your existing settings.

To remove login startup:

```cmd
Setup-Apex-Screen-Companion.cmd --uninstall
```

To install startup without starting it immediately, add `--no-start`.
Startup and native-window errors are recorded in
`%LOCALAPPDATA%\Apex\Companion\startup.log` and `desktop.log`.

### Visual explanations on your Windows screen

During an owner screen-help turn, `screen_draw` can point to a visible control
or draw a short explanation using arrows, lines, rectangles, ellipses,
polygons, curves and labels. The marks sit on a transparent **click-through**
Windows overlay, so the user can still interact with the application beneath.
They expire after 2–30 seconds and are cleared on Stop/hide or replacement.

Coordinates are normalized against the particular host screenshot, then mapped
to the captured monitor's physical bounds. The renderer uses Windows DPI
awareness and supports negative monitor origins. The tool requires that exact,
fresh frame ID and confirmation from the native renderer. A queued command is
not reported as a verified drawing. Browser/phone images and ordinary chat
cannot draw on the host desktop through this tool.

Native screen help preserves the selected **Discuss/Work** mode. Discuss can
draw explanatory marks but cannot click, type, run commands or edit user files.
Select Work for requested actions, with Apex's existing safety gates. Drawings
do not themselves execute an action or authorize one. Their location and the
explanation still depend on the vision model: an acknowledged drawing is not
proof that the model identified the correct control.

Vision turns use your existing Apex model/provider and budget configuration.
They do not become subscription-backed Codex/Claude coding turns. Use a
vision-capable model for screen questions.

### Browser Companion

1. Start Apex using your normal launcher and configuration.
2. Open the dashboard and select **Screen companion** in the sidebar. The path
   is `/companion` on the same dashboard host and port.
3. Enter your existing dashboard token if requested.
4. Click **Share screen**, choose a window, tab, or display, and check the preview.
5. Click **Look at this**, type a question, or tap **Talk**, speak, and tap it again
   to send. Recordings stop after 60 seconds.
6. Click **Float** for an always-on-top companion in browsers that support
   Document Picture-in-Picture. Keep the original tab open. Return moves the
   same interface back; it does not start another conversation.

Use a supported desktop browser over HTTPS or localhost. Phone and embedded
WebView screen-sharing support varies; the UI reports when a capability is
unavailable. The browser view remains available independently of the native window.

## What each control means

| Control | Behavior |
| --- | --- |
| Share screen | Browser permission selects the source. One JPEG snapshot is sent with each message; no host screenshot is automatically substituted. |
| Stop sharing | Ends this page's capture tracks and disables its check-ins. Historical context remains in the conversation. This does not switch off other independently configured Apex awareness services. |
| Look at this | Sends a fresh snapshot with a request to explain what matters and recommend a next step. |
| Discuss | Restricts the model to its explicit Discuss tools, including temporary native screen marks for an owner host snapshot. Commands, clicks, file edits and delegation are unavailable. Conversation persistence and approved companion memory behavior remain in effect. |
| Work | Uses Apex's existing tools and safety gates for the task you request. Tools operate on the Apex host, which can differ from the shared device. |
| Stop | Stops spoken playback immediately and requests agent cancellation. Further tool calls are skipped. A tool already executing can finish; completed actions are not undone. |
| Speak replies | Device speech by default, with optional OpenAI TTS. Turning it off also invalidates pending audio responses. |
| Check in every minute | Opt-in snapshots and Discuss turns, up to ten checks per activation. Skips busy/listening/speaking periods. Asks for one new useful observation or silence. Browser timer throttling can delay checks. |
| New chat | Starts a fresh companion thread. Existing threads remain accessible in the dashboard. |

The partner instructions replace the butler tone for companion turns only.
Apex should distinguish an observation, inference, actual test result, and
recommendation; uncertainty is allowed. These are behavioral instructions,
not a guarantee that a model will always reason or report correctly. The
tool-result panels let the user inspect evidence rather than relying solely
on the wording of a reply.

## Providers and memory

- Companion turns use the existing model router and API provider configuration.
  Choose a vision-capable model for screen questions. The text-only Claude CLI
  subscription adapter is bypassed because it cannot preserve the shared image
  or Discuss tool boundary. API use can therefore incur charges even if regular
  text chat uses a subscription. Existing budget checks remain active.
- Microphone transcription and the optional OpenAI voice reuse the dashboard's
  `/api/transcribe` and `/api/speak` endpoints. They require `OPENAI_API_KEY`.
  Device speech does not require the OpenAI TTS endpoint.
- Replies stream as text. Voice output begins after the completed reply; this
  is not a real-time voice-to-voice implementation. Tap Talk to interrupt
  playback; use Stop while the agent is thinking or working.
- A stable `companion:<thread_id>` channel retains context across turns. Text
  messages use Apex's existing conversation tables. After a server restart the
  latest 30 text messages are restored to the channel. Existing long-term
  recall remains available. This is continuity, not a new autonomous learning
  engine or proof that every earlier detail will be recalled.
- Browser images go to the selected model as conversation inputs and remain in
  working context until it is compressed. The companion does not write the
  image bytes to the conversation table. Text interpretations may remain in
  messages and memory. Provider data handling is governed by your provider
  configuration.

## Implementation

`dashboard/static/companion.*` provides the compact client. The authenticated
`/api/companion/chat` route streams newline-delimited JSON directly to its
requesting client: start, token, tool result, completion, or error. It does not
use the dashboard's global tool-observer hook to gather companion evidence.

`agent/companion.py` validates bounded JPEGs and defines the per-turn policy.
`AgentCore.run` accepts optional `screen_image` and `companion_mode` arguments;
existing callers retain their defaults. Discuss filters available tools and
rejects disallowed tool results before execution. Work uses the existing
execution path. Cancellation is checked before a queued turn starts and
between tool calls as well as during model streaming.

The shell and assets load before login; data and action routes retain dashboard
authentication. Companion POST routes additionally reject cross-origin browser
requests, including on a tokenless localhost instance. Active turns keep their
busy guard until the worker exits, including after a browser disconnect.

## Verification and remaining live checks

The automated tests cover image validation/origin, action-tool refusal in
Discuss, actual tool dispatch in Work with a mocked tool, cancellation between
tools, route authentication, cross-origin refusal, stream delivery, durable
text history, latest-message restoration, invalid requests, and concurrent
turn refusal. Existing persona, terminal interruption, provider routing,
resilience, memory, and conversation tests are also run. These tests use fake
providers and do not measure model accuracy, cost, or speech latency.

Run the focused suite with:

```bash
python -m pytest tests/test_companion.py tests/test_persona.py tests/test_tui.py tests/test_provider_routing.py tests/test_resilience.py tests/test_memory.py tests/test_conversations.py -q
node --check dashboard/static/companion.js
```

The desktop regression suite is `tests/test_screen_companion_desktop.py`.
On Windows it also creates a real transparent Tk overlay, checks click-through
styles and expiry, exercises global function-key registration/release, and
boots/stops the real WebView2 shell against a local test service. No provider
call, microphone capture or user screen capture is needed for those checks.
`scripts/check_companion_desktop_ui.cjs` covers silent boot, native authentication,
activation without a previous page click, Discuss preservation and Stop/hide.
These controlled checks do not replace laptop microphone, mixed-DPI/multi-monitor,
vision accuracy or live speech-latency acceptance.

The client DOM simulation in `scripts/check_companion_ui.cjs` also checks
stream delivery, thread reuse, mode selection, safe tool-text rendering,
snapshot attachment, capture cleanup, cancellation during the request
handshake, late audio suppression, stopping during transcription, moving
controls into and out of a floating document, and new chat. It mocks browser
media and providers, so it does not substitute for the live checks below.
With `jsdom` installed in an external test directory, run it with that
directory's `node_modules` on `NODE_PATH`:

```bash
npm install --prefix /tmp/apex-ui-check jsdom
NODE_PATH=/tmp/apex-ui-check/node_modules node scripts/check_companion_ui.cjs
```

Before treating the companion as ready for daily use, check on the configured
laptop:

- Share a clearly identifiable application and ask what is visible; verify
  that it describes the selected window, including after switching sources.
- Stop sharing and ask about a change; verify it asks for fresh context.
- Test microphone permission, transcription, both voice engines, and Stop
  during speech. Verify that stopping during transcription prevents sending.
- Open Float, type and speak there, close it, and verify controls still work
  in the original tab. Check the layout at a compact window size.
- In Discuss, request an action and verify that it cannot execute. In Work,
  request a harmless test and compare the tool-result panel with the reply.
- Stop a task containing two slow steps; verify the next step is skipped and
  the first step's outcome is reported accurately.
- Reload the page and restart Apex; ask about a recent decision and verify
  continuity. Try an optional check-in and then disable it.

The cloud browser available during implementation could not reach the local
preview server (`ERR_BLOCKED_BY_CLIENT`). Visual layout, native media capture,
floating-window behavior, and live provider behavior therefore remain desktop
acceptance checks; they are not claimed as verified.

## Platform references

- [Screen Capture API: getDisplayMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)
- [Chrome: Document Picture-in-Picture](https://developer.chrome.com/docs/web-platform/document-picture-in-picture)

Future work: full-duplex voice with automatic barge-in, relevance-ranked
check-ins beyond a fixed timer, and systematic evaluations of screen
understanding and evidence-grounded recommendations.
