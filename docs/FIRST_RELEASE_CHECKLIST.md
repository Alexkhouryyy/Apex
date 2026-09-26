# First usable spatial release

Status as of 2026-09-26: PR #3 merged at Alex's request after GitHub verification
passed (merge commit `48661fd2894ec60b10a2be612183caf6fb3b51d2`). Device,
engineering and deployed persistence acceptance remain open. Merge is not
evidence that those checks passed.
Do not describe source geometry checks or synthetic gestures as engineering
validation, real-device acceptance, or evidence of educational impact.

| Gate | Implemented and checked locally | Remaining evidence |
|---|---|---|
| Detailed reference | Pinned, licensed OpenMotor STEP; 616 valid solid occurrences; 135 selectable mesh occurrences; source hierarchy and approximate bounds | Independent review of dimensions, materials, connections and explanations |
| Manipulation | Camera-aware picking; mouse/hand movement, depth and rotation; release commit; reset/reassemble; undo | Real webcam precision, false grabs, rapid hand changes and gesture fatigue |
| Input ownership | Exclusive expiring study owner; board/gesture suppression; mouse, typing and dialog guards; stale frames cancel | Multi-window use on the laptop with its actual tracker |
| Conversation | Selected-part session context, references, view tools, both model choices, companion controls | Live microphone/STT/TTS, model correctness, interruption and latency |
| Daily workspace | Clean board, editable notes/links, object controls, focus/camera preferences, model selector, study notebook and named board switching | A sustained coding/planning workflow and a learning session with Alex |
| Persistence | Named studies and boards, independent layouts, stale-window guards, restart recovery; actual backup helper restores projects and boards | Verify DB_PATH on the deployed persistent volume and restore a deployed backup; include referenced prop files |
| Performance | Local assets, compressed CAD transfer, no hidden-tab WebGL rendering; desktop/mobile software browser checks; opt-in device timing reports | Lenovo frame rate, fan/GPU use, touch accuracy and long-session comfort |
| Release | PR #3 merged after GitHub tests, boot smoke and DOM checks passed | Verify deployment and deployed smoke checks; close the device and engineering acceptance gaps |

## Device acceptance session

1. Open a note and arrange the board using mouse/keyboard; undo it. Open the
   motor study. Confirm the board stays paused during study interaction.
2. Load each reference, orbit, isolate a part, move it across the view/in depth,
   rotate, undo and reassemble. Confirm source CAD groups and labels match.
3. Enable hands on the laptop tracker. Hover open, pinch, move, release. Try
   both hands entering/leaving, hide a hand while holding, switch windows,
   pause, and disconnect the camera. No unintended movement should commit.
4. Ask Céline to select a named part, explain it, isolate it and reassemble.
   Confirm explanations fit the selected motor and missing knowledge is stated.
5. Save notes and a modified view. Restart Apex and reopen the project. Make a
   backup with `scripts/backup_brain.py`, restore it to an isolated test DB_PATH,
   and confirm the project opens. Do not overwrite the live database to test.
6. Spend a sustained session on a real project. Record accidental grabs,
   tracking loss, voice delay, unreadable labels and movements that feel tiring.
7. Verify the deployed revision and repeat auth, asset-load, save/restart and
   companion smoke checks on the deployed host. Resolve the remaining acceptance
   gaps before describing the experience as ready for everyday engineering use.

## Record a device check

On the Lenovo, open `/study`, choose a model, expand **Device check** in the
inspector and select **Start recording**. Enable hands on the host running the
actual webcam tracker. Spend a few minutes orbiting, hovering, pinching, moving,
releasing and deliberately hiding a held hand. Click **Flag accidental grab**
when one occurs. Then **Stop recording** and **Download report**. Repeat with
the other model. Also note lighting, camera position, comfort and visible errors
separately; the report cannot infer them.

The report includes visible frame intervals, hand-request round trips, detector
sample ages, stale episodes, blocked samples, acknowledged hand actions and
cancelled/failed actions. Timing distributions use the latest 600 observations;
counters cover the recording. Repeated stale samples count as one episode until
tracking is fresh again. A hand that leaves the camera with otherwise fresh
tracking is not a stale episode, though losing it during a grab cancels the grab.
Mouse edits are excluded from hand-action counts. Stopping during a grab or
pending request can leave a grab without a recorded outcome.

Recording is opt-in and stays in tab memory until reload. Starting again replaces
it; changing models stops it. Downloads contain no frames, landmarks, coordinates,
tokens, session/project identifiers or notes. Nothing is automatically uploaded.
Hidden-tab time is excluded from frame intervals. Request round trip and detector
age are separate measurements, not end-to-end gesture latency. There is no
automatic quality grade. A synthetic browser test checks the reporting plumbing;
only a session with a real tracker supplies device evidence.

No public engineering-accuracy or learning-outcome claim is supported yet.
Arbitrary model generation, quantitative simulation, native desktop control,
Quest support and multi-user isolation remain outside this first release.
