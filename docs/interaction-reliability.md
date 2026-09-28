# Board interaction and perception

Open your thumb and index finger once when a hand enters view. Close them
and hold briefly to grab: Apex confirms the closure over at least three frames
and 100 ms before the board's grab dwell. Each hand arms independently.
The HUD explains whether a hand is arming, confirming a pinch, holding,
or missing depth. Missing depth cannot start a grab. Existing holds tolerate
a short depth dropout; longer loss requires opening the hand again.

A held board object takes precedence over global wave/pinch microphone actions.
Those actions explain the board hold instead of reporting a missing microphone.

Click a model's visible geometry, or make a brief pinch tap, to select a mesh
component. The selected region is highlighted and travels into Apex's conversation
context. This works with imported GLBs as well as recipe-built models. Disconnected
pieces in merged geometry remain selectable. A fused mesh without part boundaries
gets a surface-region selection, not an invented engineering part name. Very large
meshes (over 200,000 triangles) use the clicked triangle to bound selection work.
Selection does not turn a fused mesh into editable CAD parts. Recipe parts mode
still provides its existing editing controls; whole-object transforms remain whole-object.

The browser reports current model silhouettes for hand targeting, so a large
model can be reached at its visible edge rather than only near its centre.
Reports are tied to the workspace, model version, hand identity and position,
and expire after 350 ms. Without a current browser report, centre targeting remains.

File awareness ignores dependency directories, Git metadata, `.wrangler`, caches,
build output, lock files and editor temporary files before logging events.
Add semicolon-separated globs in `.env`, for example:

```dotenv
AWARENESS_WATCH_IGNORE_GLOBS=**/generated/**;**/recordings/**
```

Paths are matched with forward slashes, case-insensitively. Custom patterns add
to the defaults. Restart Apex after changing them. Repeated identical file events
within one second are collapsed; real source edits, deletions and renames remain visible.
