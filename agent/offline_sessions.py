"""Offline conversations pinned to the same projects and handoffs as Apex Home."""
import time
from agent import longterm, board_workspaces, conversations, continuity


def session(workspace_id=None, *, create=False):
    board_workspaces.ensure_db()
    conversations.init_db()  # Fail visibly if storage is unavailable.
    continuity.init_db()
    with longterm._conn() as db:
        db.execute('CREATE TABLE IF NOT EXISTS offline_project_threads ('
                   'workspace TEXT PRIMARY KEY, thread INTEGER NOT NULL)')
        if create:
            db.execute('BEGIN IMMEDIATE')
        projects = [dict(id=r[0], name=r[1]) for r in
                    db.execute('SELECT id,name FROM board_workspaces ORDER BY created,id')]
        if workspace_id is None:
            workspace_id = db.execute("SELECT value FROM workspace_settings WHERE key='active'").fetchone()[0]
        if not isinstance(workspace_id, str) or workspace_id not in {p['id'] for p in projects}:
            raise ValueError('Choose an existing Apex project.')
        row = db.execute('SELECT t.thread FROM offline_project_threads t JOIN chat_threads c ON c.id=t.thread '
                         'WHERE t.workspace=?', (workspace_id,)).fetchone()
        tid = row[0] if row else None
        if tid is None and create:
            name = next(p['name'] for p in projects if p['id'] == workspace_id)
            now = time.time()
            tid = db.execute('INSERT INTO chat_threads (title,created_at,updated_at) VALUES (?,?,?)',
                             ('Offline · '+name, now, now)).lastrowid
            db.execute('INSERT OR REPLACE INTO offline_project_threads VALUES (?,?)', (workspace_id, tid))
        if tid is not None:
            # Also pins this conversation when it is opened from Chat or Companion.
            db.execute('INSERT OR IGNORE INTO continuity_channels VALUES (?,?)', ('thread:'+str(tid), workspace_id))
            bound = db.execute('SELECT workspace FROM continuity_channels WHERE channel=?', ('thread:'+str(tid),)).fetchone()[0]
            if bound != workspace_id:
                raise RuntimeError('The saved conversation belongs to another project.')
    return dict(projects=projects, project=continuity.project(workspace_id), thread_id=tid,
                messages=conversations.messages(tid, limit=200, newest=True, strict=True) if tid else [])
