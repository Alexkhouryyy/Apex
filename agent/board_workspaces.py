"""Named boards on one Apex host. Each board owns its storage and undo history.

Switching is shared by dashboard clients. Cached board instances keep in-flight
operations tied to their original storage; browser writes also carry an epoch.
"""
import time
import uuid
from agent import longterm

_boards = {}


class Conflict(ValueError):
    pass


def ensure_db():
    from agent import board
    board.init_db()
    with longterm._conn() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS board_workspaces (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS workspace_cards (
            workspace_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, src TEXT NOT NULL,
            x REAL NOT NULL, y REAL NOT NULL, scale REAL NOT NULL, rot REAL NOT NULL,
            created REAL NOT NULL, PRIMARY KEY(workspace_id,id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS workspace_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)''')
        if not db.execute("SELECT 1 FROM workspace_settings WHERE key='migrated'").fetchone():
            db.execute("INSERT INTO board_workspaces VALUES ('default','My workspace',?)", (time.time(),))
            db.execute("INSERT INTO workspace_cards SELECT 'default',id,kind,title,body,src,x,y,scale,rot,created FROM board_cards")
            db.execute("INSERT INTO workspace_settings VALUES ('active','default')")
            db.execute("INSERT INTO workspace_settings VALUES ('migrated','1')")
        # Original board_cards is retained as a migration-time recovery copy.


def _load(workspace_id):
    from agent.board import Board
    with longterm._conn() as db:
        row = db.execute('SELECT name FROM board_workspaces WHERE id=?', (workspace_id,)).fetchone()
    if row is None:
        raise ValueError('Workspace not found.')
    b = Board(); b.persist = True
    b.workspace_id, b.workspace_name = workspace_id, row[0]
    b.restore(strict=True)
    b.hands_enabled = False
    return b


def load_initial():
    ensure_db()
    with longterm._conn() as db:
        active = db.execute("SELECT value FROM workspace_settings WHERE key='active'").fetchone()[0]
    _boards.clear()
    b = _load(active); b.workspace_epoch = uuid.uuid4().hex
    _boards[active] = b
    return b


def check_context(b, context):
    if b.workspace_id is None:  # Bare Boards in existing unit/integration callers.
        return
    if not isinstance(context, dict) or context.get('id') != b.workspace_id or context.get('epoch') != b.workspace_epoch:
        raise Conflict('The active workspace changed. Wait for the board to update, then try again. Your draft is preserved.')


def listing():
    from agent.board import get_board
    active = get_board().workspace_context()
    with longterm._conn() as db:
        rows = db.execute('''SELECT w.id,w.name,COUNT(c.id) FROM board_workspaces w
            LEFT JOIN workspace_cards c ON c.workspace_id=w.id GROUP BY w.id ORDER BY w.created''').fetchall()
    return dict(active=active, workspaces=[dict(id=r[0],name=r[1],items=r[2]) for r in rows])


def create(name, copy_current, context):
    from agent import board, study_input
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('Give this workspace a name of 1–80 characters.')
    if type(copy_current) is not bool:
        raise ValueError('Choose whether to copy the current board.')
    with study_input.LOCK, board._board_lock:
        old = board.get_board(); check_context(old, context)
        if copy_current:
            old.set_hands_enabled(False)
        workspace_id = uuid.uuid4().hex
        with old._lock:
            snapshots = [old._snapshot(c) for c in old._cards] if copy_current else []
        with longterm._conn() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM board_workspaces').fetchone()[0] >= 50:
                raise ValueError('This host has reached its limit of 50 workspaces.')
            db.execute('INSERT INTO board_workspaces VALUES (?,?,?)', (workspace_id,name.strip(),time.time()))
            destination = board.Board(); destination.workspace_id = workspace_id
            for snap in snapshots:
                snap['id'] = uuid.uuid4().hex[:8]
                destination._save_row(db, snap)
        return dict(id=workspace_id, name=name.strip(), items=len(snapshots))


def switch(workspace_id, context):
    from agent import board, study_input
    if not isinstance(workspace_id, str):
        raise ValueError('Choose a workspace.')
    with study_input.LOCK, board._board_lock:
        old = board.get_board(); check_context(old, context)
        if workspace_id == old.workspace_id:
            return old.workspace_context()
        if study_input.active():
            raise ValueError('Pause study hand controls before switching workspaces.')
        target = _boards.get(workspace_id) or _load(workspace_id)
        old.set_hands_enabled(False)
        old._persist_all(strict=True)
        target.set_hands_enabled(False)
        with longterm._conn() as db:
            db.execute("UPDATE workspace_settings SET value=? WHERE key='active'", (workspace_id,))
        target.workspace_epoch = uuid.uuid4().hex
        _boards[workspace_id] = target
        board._board = target
        return target.workspace_context()
