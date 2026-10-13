# Screen Companion interaction evidence

## Target and method

REA 6.4.0 analyzed an exact copy of the Apex-served `companion.js`,
`speech_queue.js`, `companion.html` and `companion.css` at the merged
`db8d25e862f33a2e0c807492d54d146e973bc09a` baseline. This is a focused
application-resource snapshot, not the complete Python repository or a
decompiled Wispr Flow/Clicky executable. The supplied video establishes the
desired visible interaction; it does not reveal those products' implementation.

The investigation used the project-local public REA CLI, without Ghidra, IDA,
personal Codex configuration changes or access to the laptop. Complete canonical
output is retained in the task workspace under
`/workspace/screen-companion-reference/rea-interaction/`.

| Question | Evidence | Result and boundary |
| --- | --- | --- |
| Which client resources and static relationships exist? | `ev_69af35041c5c734ea1aa1a295703888b2fa96ebd26d1346061e1eca479aca796`, root SHA-256 `09999818f2532bd178605bae4bc5e943991b83a926048fe5500b2b3c4914720c` | REA parsed three JavaScript/HTML sources as inert text. Static relationships do not establish execution. |
| What leaves the Companion module? | `ev_cb4584ce65ba01318be1a638ae49dd2109fd514013f893af2cefc4ffbd365a90` | Exact module seed, outgoing traversal: 18 nodes, 34 edges, 35 observed and 34 inferred facts. Containment relationships are not proof that every reached node participates in dictation. |
| Does a literal transcription route resolve statically? | `ev_b1bec14491131a491d3304c96fbe8db0348eb3e7121a9b98c47f586aa15d8024` | No matching seed. The URL is constructed dynamically; this negative result does not mean transcription is absent. Ordinary source inspection and controlled behavior fill that gap. |
| Does native client activation remain silent until invoked? | `ev_f3cca901633753ef04d4b8ce34fea9fa0afd4e853988102fff9d435066bb800c` | REA process capture of `check_companion_desktop_ui.cjs` exited 0. Actual client JS, simulated native bridge/microphone/provider. This does not prove physical microphone or Windows focus behavior. |
| Can the new client route dictation without requesting a chat answer? | `ev_38ba9d428ec8d2bc5b80d787bc41a6b7bb2095142a79b0ec745a9035a776e782` | REA process capture of the dictation client harness exited 0, covering Unicode transcript routing, changed-field recovery, cancellation and drawing entry with simulated native input. Later checks additionally cover replacement of a microphone session while transcription is pending. |

REA process capture observes process output and sampled process state. It does
not observe network activity. Assertions that no screen/chat/TTS request occurs
come from the client harness's explicit request instrumentation. No filesystem
observation paths were selected; filesystem effects remain unknown in those
REA records. Python source enforcement is ordinary repository evidence, not REA
decompilation evidence.

## Changes grounded in the findings

Previously the voice transcript was submitted as a Companion chat turn. The
new explicit Dictate action reuses the transcription path and routes each
utterance to a native, user-started session pinned to a foreground window,
process, native control and UI Automation field identity. It rejects changed focus, password fields, stale generations and
control keys that could submit forms. It never requests an agent reply or host
screenshot for that utterance. A cancelled or replaced microphone session
cannot reuse a late transcript.

Previously `screen_draw` provided bounded, temporary AI explanations only.
The separate user sketch surface supplies pen, boxes, arrows, text, undo/redo
and SVG saving. Done makes marks click-through and visible in the next owner
screen question. Its controls and the AI guidance surface stay excluded from
captures. Discuss retains its existing restrictions on host actions; user
drawing is direct manipulation rather than an agent command.

## Verification and remaining unknowns

`tests/test_companion_creation.py` includes real Windows acceptance for Unicode
SendInput into an owned text field and for editing/done styles plus capture of
a pen stroke. `tests/test_screen_companion_desktop.py` covers the real native
WebView, function key and AI overlay. The dedicated Windows/Linux workflow runs
these with the Companion and resident regressions. The DOM dictation harness
checks both Stop and replacement of a pending transcription session.

Hardware acceptance still covers microphone permission and transcription
quality, media/Fn keys, multi-monitor layout, latency, target-app input support
and Windows login startup. A focus check reduces accidental insertion but
cannot make Windows focus changes and SendInput atomic. Review partial text
after a failure. A captured user mark does not establish that the model
understood it correctly. Screen discussion takes snapshots per question,
not a continuous video stream. The canvas is an annotation/sketch tool; it is
not a complete design suite or automatic app implementation engine.

## Reproduce

Install the pinned project-local CLI with `bash scripts/setup_rea_cloud.sh`.
Copy the four named baseline resources into a new directory, then use:

```bash
node integrations/rea/node_modules/rea-agents/scripts/rea.mjs analyze-javascript-application /absolute/path/to/copied-resources --format json > application.json
```

The CLI feature-trace input takes the complete `application` Evidence object,
an exact returned node-ID seed and `direction: "outgoing"`. It differs from
the MCP retained-reference schema. `capture-process` takes a scenario JSON
with an existing Node executable, the harness path, repository working
directory, `NODE_PATH` for jsdom and bounded timeout/idle timeout. Keep full
output on disk and inspect summaries rather than loading the full graph into
chat. Preserve the tool's limitations with any shared report.
