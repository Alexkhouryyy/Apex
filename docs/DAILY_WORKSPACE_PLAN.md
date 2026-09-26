# Apex daily workspace

Goal: Alex should want Apex present throughout real work. Hands, mouse, keyboard
and voice are complementary inputs. A calm interface, dependable selection,
recoverable actions and continuity matter more than decorative effects.

## Delivery sequence

1. **Comfortable workspace foundation (this branch).** Clean digital canvas,
   optional camera backdrop, quiet hand indicators, hidden-by-default setup,
   object list, notes, selection controls, mouse/touch dragging, keyboard nudges,
   undo/redo, focus layout, and a server-side hand-action pause. Keep existing
   voice/parts/calibration/export capabilities. UI preferences persist locally;
   content uses the existing board database. A Railway volume is still required
   for durable cloud storage. This is a browser workspace, not a desktop overlay.
2. **Explorable assemblies and dependable spatial manipulation.** The first
   [motor study](ASSEMBLY_STUDY.md) is implemented on this branch: a separate
   mouse/touch 3D view, 14 component groups, reversible exploded views,
   sourced explanations and selected-part companion context. It is educational
   geometry. A second, source CAD reference now exposes 135 mesh occurrences,
   source hierarchy and approximate sizes. Camera-aware hand and mouse part
   manipulation are implemented with ownership and cancellation. Independent
   engineering review and physical-device acceptance are still pending.
   Tune against Alex's recorded gestures;
   measure false grabs, target selection, release success and perceived delay.
   Add visible hover/armed/grabbed states and resolve input ownership. Implement
   camera orbit, depth, axis constraints and physical versus view transforms
   together with matching picking. Preserve undo and existing assets.
3. **Project continuity.** Named assembly studies now persist component notes,
   camera and assembly view in the existing database, with explicit save state,
   conflict detection and model-revision compatibility checks. Restart recovery
   is verified locally. General named workspaces, project-linked files/notes/links,
   resumable layouts, version history and explicit save/error feedback. Test
   restart, backup and restore on both laptop and cloud.
   [Named board workspaces](NAMED_WORKSPACES.md) are now implemented with separate
   item/layout storage, per-board process-local undo, optional copies, shared
   switching, stale-window guards, last-active restart and backup restoration.
   Deployed persistence and real laptop acceptance remain to be verified.
   Board notes can now be edited, and web reference links can be created,
   edited and reopened explicitly. Both use board storage and content undo/redo.
   Content fingerprints reject stale editor saves while allowing independent
   position changes. A conflict preserves the draft and offers a new copy;
   explicit content saves report storage errors. Cloud/laptop synchronization
   remains unfinished.
4. **Daily work integration.** Existing applications remain usable. Start with
   supported links/files and explicit screen-sharing context; evaluate a native
   companion/overlay for Windows. A browser cannot embed/control arbitrary
   desktop apps or bypass sites' iframe restrictions. Require truthful tool
   evidence for every app action.
5. **Polish against real tasks.** Complete an Apex coding task, a Sky Light
   planning task and a model-editing task. Remove unnecessary gestures and UI.

## First milestone acceptance

- With no camera, create/select/move a note and undo/redo it.
- Move a model by its label; resize and turn its view with visible controls.
- Focus and camera-background preferences survive reloads.
- Paused hand controls cannot grab, point at objects, or trigger gesture actions;
  pausing during a hold refuses and asks for release. It does not stop the camera.
- Voice and parts workflows keep their existing behavior.
- No claim of all-day readiness from simulated tests. Real laptop camera,
  touch/voice, long-session comfort and GPU checks remain user acceptance.

## Deliberate limits of milestone 1

No arbitrary-app embedding, native overlay, project switching, camera orbit,
new 3D depth editing, imported mesh segmentation or cloud/laptop synchronization.
Mouse dragging moves whole objects using their cards/labels; existing hand
controls edit generated model parts. Hand pause is shared across board clients
and resets on server restart. Only presentation preferences are per browser.

## Preview and verification

![First workspace milestone with example notes and a generated model](images/daily-workspace-preview.png)

The screenshot uses example content in an isolated local server. Chromium
verified authenticated note creation, mouse drag and undo, hand pause, focus
preference restoration, model rendering, and a 390px viewport without horizontal
overflow or JavaScript page errors. This does not measure real camera/voice
latency, laptop GPU performance or long-session comfort.

Targeted Python checks: `python -m pytest tests/test_daily_workspace.py
 tests/test_board.py tests/test_board_jarvis.py tests/test_board_parts.py
 tests/test_board_tap.py tests/test_handtrack.py -q` (run as one command).
UI checks: `node scripts/check_daily_workspace_ui.cjs` and existing board
HUD/parts/voice/companion/drive checks; jsdom must be available on NODE_PATH.

The optional `node scripts/check_workspace_browser.cjs` checks the real server
and browser with an isolated database: note editing, content undo/redo, a second
writer's conflict, copying a preserved draft, safe link controls, process restart
and mobile creation. It uses the same Playwright/Chromium and Python runtime
overrides as `scripts/check_study_browser.cjs`.

### Notes and references

Choose **New note** or **Add a link**. On a narrow screen use the **+** dock button
and select the item type. Select an existing note/link and use **Edit note/link**.
Links require an HTTP/HTTPS address without credentials and open in a separate
tab only through the visible **Open** control. Saving, selecting or moving one
does not fetch or embed its contents. Notes are plain text (80-character titles,
600-character bodies); web addresses may contain up to 2048 characters.

Save is explicit. The editor keeps failed drafts, guards closing with unsaved
changes, and disables editing while a save is pending. If content changed in
another window, reopen the current item or choose **Save draft as a new item**.
The editor never silently replaces newer content. Undo history is process-local
and shared by this board; saved content uses the host's existing SQLite database.
New text/link items refuse a full board instead of evicting an older item.
Legacy tracker writes and history persistence remain best-effort; strict storage
error handling applies to explicit note/link saves and flushing before a workspace
switch. See [Named workspaces](NAMED_WORKSPACES.md) for switching and storage scope.

Release gate tracking: [FIRST_RELEASE_CHECKLIST.md](FIRST_RELEASE_CHECKLIST.md).
