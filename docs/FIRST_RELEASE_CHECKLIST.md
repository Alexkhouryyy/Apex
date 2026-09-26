# First usable spatial release

Status as of 2026-09-26: implementation advanced; release gates remain open.
Do not describe source geometry checks or synthetic gestures as engineering
validation, real-device acceptance, or evidence of educational impact.

| Gate | Implemented and checked locally | Remaining evidence |
|---|---|---|
| Detailed reference | Pinned, licensed OpenMotor STEP; 616 valid solid occurrences; 135 selectable mesh occurrences; source hierarchy and approximate bounds | Independent review of dimensions, materials, connections and explanations |
| Manipulation | Camera-aware picking; mouse/hand movement, depth and rotation; release commit; reset/reassemble; undo | Real webcam precision, false grabs, rapid hand changes and gesture fatigue |
| Input ownership | Exclusive expiring study owner; board/gesture suppression; mouse, typing and dialog guards; stale frames cancel | Multi-window use on the laptop with its actual tracker |
| Conversation | Selected-part session context, references, view tools, both model choices, companion controls | Live microphone/STT/TTS, model correctness, interruption and latency |
| Daily workspace | Clean board, notes, object controls, focus/camera preferences, model selector and study notebook | A sustained coding/planning workflow and a learning session with Alex |
| Persistence | Named studies, camera and component transforms, conflict checks, restart recovery; actual backup helper restores projects | Verify DB_PATH on the deployed persistent volume and restore a deployed backup |
| Performance | Local assets, compressed CAD transfer, no hidden-tab WebGL rendering; desktop/mobile software browser checks | Lenovo frame rate, fan/GPU use, touch accuracy and long-session comfort |
| Release | Draft PR with tests, documentation, sources and reproducible browser check | Resolve above blockers, final review, merge/deploy and deployed smoke check |

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
7. Only after blockers are resolved: review the PR, merge/deploy, and repeat
   auth, asset-load, save/restart and companion smoke checks on the deployed host.

No public engineering-accuracy or learning-outcome claim is supported yet.
Arbitrary model generation, quantitative simulation, native desktop control,
Quest support and multi-user isolation remain outside this first release.
