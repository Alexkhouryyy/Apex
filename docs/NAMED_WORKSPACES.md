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
assets. Back up those files as well as the database. Motor study projects remain
separate and can be reached through saved web links; switching boards does not
automatically open a study or restore its camera. Camera/focus presentation
preferences are still browser-wide. This increment does not add workspace
renaming/deletion, cloud/laptop synchronization or arbitrary desktop app layouts.

`scripts/backup_brain.py` includes all workspaces and active-workspace metadata in
its SQLite backup and reports workspace/item counts. Cloud persistence still
requires the database on a persistent volume. Test restores with a separate
database path, never by overwriting live data.

## Verification

`tests/test_board_workspaces.py` covers migration, independent storage/history,
copied identifiers, late writes to the outgoing board, stale-window requests,
held objects, storage failure, restart and an actual backup restoration.

`scripts/check_workspace_browser.cjs` uses an isolated server and two real browser
pages. It checks draft preservation across switching, live window updates, copied
layouts, independent items, active-space restart and the mobile switcher, alongside
the note/link editing flow. This does not validate physical webcam usability.
