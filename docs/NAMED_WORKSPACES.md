# Named workspaces

Use the workspace name in the board header to open **Your workspaces**. Create
an empty board or choose **Copy this board’s items and layout**. Names can be up
to 80 characters. There are at most 50 spaces on one Apex host.

Each space keeps its own notes, reference links, model/image references and
object positions, scales and rotations. Returning to a space restores these
items. Each loaded board has its own undo/redo stack for the lifetime of the
process; undo history is not saved across restarts. The last active space is
restored after a restart, with hands paused.

The first upgrade copies the existing board into **My workspace** in an atomic,
one-time migration. The original `board_cards` table remains as a migration-time
recovery copy; it no longer reflects ongoing edits. Do not treat running an older
Apex version as a way to restore current workspace data.

## Switching safely

- The active workspace is shared by dashboard windows on this host. A switch
  updates their live views; it is not a separate workspace per tab or per user.
- Switching pauses hand actions. A held object or active study hand controller
  must be released/paused first. Enable board hands explicitly when ready.
- The outgoing board is flushed to SQLite before the active workspace changes.
  Storage failures cancel switching. Failed restores do not replace the current
  board with an empty one.
- Browser edits, history actions, selections and part-mode requests carry the
  active workspace identifier and an activation token. A stale window cannot
  write to the newly active board. Switching back also changes this token.
- A draft open in another window remains visible. Its old-workspace save is
  rejected; explicitly choose **Save draft in the current workspace** to create
  a new item there. Pointer drags are cancelled on a workspace change.
- Board instances retain their own storage identity. An operation already holding
  a previous board instance writes to that board, not the new one. New assistant
  tool calls still resolve the active board at execution time; switching does not
  cancel an ongoing conversation or make its future commands workspace-bound.

Content changes use existing board persistence. Explicit note/link saves and
workspace switching surface storage failures. Legacy tracking/history writes
remain best-effort. A successful switch flushes the outgoing board's memory.

## Scope and storage

This implementation supports one Apex server process per database. Keep the
existing single-worker deployment. It does not coordinate active workspaces or
in-memory history across multiple server workers.

Model copies retain references to existing prop files rather than duplicating
assets. Back up those files as well as the database. Saved motor studies can now
be linked directly from their Projects dialog and opened from the board's
workspace menu. Switching boards does not automatically open a study. Camera/focus presentation
preferences are still browser-wide. This increment does not add workspace
renaming/deletion, cloud/laptop synchronization or arbitrary desktop app layouts.

`scripts/backup_brain.py` includes all workspaces and active-workspace metadata in
its SQLite backup and reports workspace/item counts. Cloud persistence still
requires the database on a persistent volume. Test restores with a separate
database path, never by overwriting live data.

## Connect a motor study

1. In Motor study, save your notes and view through **Projects**.
2. Choose a named workspace under **Add this study to a workspace**, then click
   **Add to workspace**. Save unsaved changes first. Choosing a destination does
   not switch the board's active workspace.
3. On the board, open the workspace menu. **Studies · [workspace name]** lists
   references for the active space. **Open study** opens a new tab and restores
   the latest saved study, including notes, component transforms and camera.
4. **Remove reference** removes only the workspace association. The saved study
   remains in the study project's list and can be added again.

References point to the same saved project; they are not frozen copies. A later
study save is reflected in every linked workspace. Copying a board also copies
its study references without duplicating the study. Duplicate additions are
idempotent, and each space can reference up to 100 studies. A stale project version
is rejected when adding a reference; reopen its latest saved version before
trying again. A model-revision mismatch is displayed without opening or deleting
the preserved project. Refresh the workspace menu to see references changed in
another window. Database backups include these associations and study contents.

## Verification

`tests/test_board_workspaces.py` covers migration, independent storage/history,
copied identifiers, late writes to the outgoing board, stale-window requests,
held objects, storage failure, restart and an actual backup restoration.

`scripts/check_workspace_browser.cjs` uses an isolated server and two real browser
pages. It checks draft preservation across switching, live window updates, copied
layouts, independent items, active-space restart and the mobile switcher, alongside
the note/link editing flow. It also saves a real study, adds it to a workspace,
restarts the server, reopens the study from the board with saved notes/view, and
removes the reference without deleting the project. This does not validate
physical webcam usability.
