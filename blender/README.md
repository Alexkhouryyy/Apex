# Apex Bridge — Blender add-on

Lets you say "Apex, create a red cube, 50 millimetres wide" and have a real,
measured object appear on Apex's glass board.

## What this is, and isn't

Blender is a real application Apex cannot launch or bundle — this add-on runs
*inside* your own Blender, and Apex talks to it over one connection on
`localhost`. It is not a second Apex service, and it does not run arbitrary
code sent from Apex: it accepts exactly four things — "are you there", "make
this primitive shape", "set this object's colour", "export this object as a
GLB" — and refuses everything else. If you're comparing this to a full
Blender-MCP-style bridge, this is deliberately narrower: no free-form Python
execution, on purpose.

## Install

1. Open Blender (3.6 or newer).
2. **Edit > Preferences > Add-ons > Install...**
3. Pick `apex_blender_addon.py` from this folder.
4. Enable the checkbox next to "Apex Bridge".
5. In the 3D viewport, press **N** to open the sidebar, find the **Apex** tab.
6. Click **Start Apex Bridge Server**. It should say "listening on
   127.0.0.1:8799".

Leave Blender open with the server running — it's not a background
service, it's this add-on inside your open Blender session.

## Enable it in Apex

In `.env` (via `scripts\set_env_key.py`, never a raw edit — see the main
README):

```
BLENDER_ENABLED=true
```

Restart Apex. Then just talk to it:

- "Apex, create a red cube, fifty millimetres wide."
- "Make it metallic blue."
- (pinch it, drag it, two-hand scale it on the board, same as any other model)

## Where files go

Exports land in `~/.apex/blender_exports/` (created automatically), then Apex
copies each one into its own props jail under `created/` before putting it on
the board. Nothing is ever overwritten: a recolor exports a brand-new file
rather than replacing the last one, so a bad result never costs you the good
one that came before it.

## What's deliberately not here yet

Undo/redo inside Blender, composite/parametric objects (a phone stand with
mounting holes, not just a primitive), the physical projector table, and
3D-printing submission. All of that was in the original design document this
was scoped from and all of it was explicitly deferred there too — this add-on
covers exactly the "voice creates a primitive, hands manipulate it" loop.
