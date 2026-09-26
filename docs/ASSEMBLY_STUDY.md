# Apex assembly study

The first study is an original, simplified permanent-magnet brushed DC motor.
Its 14 named component groups can be selected, separated, isolated, hidden and
reassembled. The graphite/cyan workspace keeps the model central, with a
searchable component tree and a contextual inspector.

![Motor separated into component groups](images/assembly-study-preview.png)

## Use it

Open **Motor study** from `/board`. This pauses board hand actions before
navigating; release any held board object first. Returning to the board leaves
hands paused until you resume them. Direct navigation to `/study` is also
supported but does not change board hand controls in other windows.

Drag to orbit, scroll to zoom, or use touch orbit/pinch. Click a component in
the view or list. Use **Take apart**, the separation slider, **Isolate**,
**Hide**, **Show all**, **Reassemble**, and undo/redo. **Section** clips the
geometry without capping the cut. **Rotor motion** is illustrative and requires
the model to be assembled. Reset view restores the camera.

**Ask Céline** opens the existing companion with the selected study session.
The server supplies the current part, sources and model limitations for each
turn. The `assembly_study` tool lets the companion open the motor study or
change its selection/view. An active board follows the open-study event.
For example, ask to open a brushed DC motor, then ask to isolate its commutator.
Actual microphone, provider response quality and voice latency still need
testing on the user's device; tool execution and context delivery are tested.

**Your notebook** holds separate notes for the selected component (or the
overview when nothing is selected). **Projects → Save study** saves a named
snapshot; **Save a copy** preserves another version as a separate project.
Opening a project restores notes, selection, visibility, separation, section,
camera and rotor angle, with motion paused. Refreshing a saved project URL also
restores the last saved snapshot. These are explicit saves, not autosave.
The header shows unsaved changes. Notes remain personal text and are not
automatically sent to the companion.

Saved projects use Apex's existing SQLite database (`DB_PATH`). They survive
process restarts when that file persists; Railway still needs a persistent
volume and backups. Updates use optimistic versions: a stale tab cannot
overwrite a newer save. On conflict, save a copy or reopen the latest project.
Versions detect conflicts; they are not a browsable historical archive.
Failed saves and cancelled/failed opens preserve the current notebook.

**Engineering readiness** records the model's purpose, geometry provenance,
dimension/material/physics limits and engineering-review status. The current
motor has no independent engineering review. Saved projects pin the manifest
hash (including its geometry revision). Incompatible revisions remain stored
but cannot be opened automatically. Any geometry change must bump
`geometry_revision` in the manifest; no migration or old-model asset archive
exists yet. The viewer sends its loaded model hash when saving, so a stale
page cannot silently save against a new model definition.

## Fidelity and persistence

- Geometry is authored for explanation, not manufacturer CAD or fabrication.
  It is not to scale and contains no validated dimensions or tolerances.
- Windings are grouped illustrations; electrical connections, brush timing,
  magnetic fields, loads and performance are not simulated.
- This milestone supports one curated motor. It does not generate arbitrary
  detailed assemblies from a prompt or split an arbitrary mesh into real parts.
- Study navigation uses mouse/touch. Hand control in this camera/view is still
  pending. The board's existing gesture controller is a separate view.
- Unsaved sessions and undo history are in memory: a restart or eviction loses them.
  There are at most 32 recent sessions, with 40 undo steps per session. Reloading
  an expired session opens a new assembly and reports that reset.
- Named project snapshots persist separately. The latest 100 appear in Projects;
  no user accounts, cross-host sync, project deletion or revision-history UI is
  provided yet. Existing host backups include the study table.
- The study uses the existing shared dashboard token. It is not multi-user
  tenant isolation. Model/session APIs require authentication; mutations check
  request origin. Static shell/Three.js assets are served locally.

Component descriptions cite Nidec's motor glossary in the inspector and model
manifest: [DC motor construction](https://www.nidec.com/en/technology/motor/glossary/item/construction_of_a_dc_motor/),
[commutator](https://www.nidec.com/en/technology/motor/glossary/item/commutator/),
[commutation](https://www.nidec.com/en/technology/motor/glossary/item/commutation/),
and [carbon brush](https://www.nidec.com/en/technology/motor/glossary/item/carbon_brush/).
These support component explanations, not the dimensions of this original model.

## Verification and next acceptance

Python checks cover part/source consistency, reversible views, independent and
bounded sessions, invalid commands, auth/origin checks, server-resolved context
and tool execution. Run `python -m pytest tests/test_assembly_study.py tests/test_study_projects.py -q`.
Project tests also exercise a fresh Python process, concurrent save conflicts,
invalid/oversized data, stale views and model-revision changes.
The companion DOM check `node scripts/check_companion_in_board_ui.cjs --assembly`
checks trusted messages and the session sent with the question (requires jsdom).
`node scripts/check_study_projects_ui.cjs` checks failed saves/opens, safe text
rendering, unsaved-change protection and edits made during a save.

A real Chromium/real server check exercised loading all 14 components,
explosion, selection, isolation, hide/reassemble, rotor/section/undo, reflected
server commands, companion opening and a 390px viewport without horizontal
overflow or JavaScript page errors. The preview uses isolated example data.

`node scripts/check_study_browser.cjs` is an optional reproducible Playwright
check with isolated data. It starts the real server, saves component notes and
view state, restarts the server, reloads the saved project and compares the
restored camera/angle. It checks desktop/mobile layout and readiness text.
Install Playwright and Chromium for this check; optional runtime overrides are
`APEX_TEST_PYTHON` and `APEX_CHROMIUM_PATH`. It is not part of the fast DOM CI job.

## Toward serious engineering study

The current view supports component identification and introductory discussion.
It is not sufficient for design approval, manufacturing, load calculations or
quantitative motor analysis. Publishing it does not establish teaching impact.

Next gates:

1. A licensed, validated assembly asset with explicit units, dimensions,
   nested part hierarchy, materials, connections and a source/version record.
   Preserve original geometry and distinguish measured from illustrative data.
2. Camera-aware hand selection with explicit input ownership, stable grabbing,
   release recovery and undo. Verify on real laptop gestures, not only mocks.
3. A reviewed lesson and a small engineer/student pilot: identify components,
   explain commutation and recover a saved study. Compare completion errors
   and understanding against conventional diagrams before claiming benefit.

Physics or engineering calculations need a separate validated solver and
reference cases. More visual detail alone cannot establish engineering accuracy.
