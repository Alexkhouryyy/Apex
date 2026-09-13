"""Reconnectable companion turns, owned by the single Apex dashboard process.

Disconnecting a browser never retries an action. A host restart marks unfinished
turns interrupted instead of replaying a potentially completed side effect.
"""
import json
import threading
import time

from agent import longterm

_ready_for = None
_lock = threading.Lock()


def ensure_db():
    global _ready_for
    with _lock:
        if _ready_for == str(longterm.DB_PATH):
            return
        with longterm._conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS companion_jobs (
                id TEXT PRIMARY KEY, thread_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, message TEXT NOT NULL,
                status TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            db.execute("""UPDATE companion_jobs SET status='interrupted',
                error='Apex restarted before completion was recorded. Check results before retrying.',
                updated_at=? WHERE status='running'""", (time.time(),))
        _ready_for = str(longterm.DB_PATH)


def create(turn_id, thread_id, fingerprint, message):
    ensure_db()
    with longterm._conn() as db:
        db.execute("""INSERT INTO companion_jobs
            (id,thread_id,fingerprint,message,status,created_at,updated_at)
            VALUES (?,?,?,?,'running',?,?)""",
            (turn_id, thread_id, fingerprint, message, time.time(), time.time()))


def update(turn_id, *, text, evidence, status='running', error=''):
    with longterm._conn() as db:
        db.execute("""UPDATE companion_jobs SET text=?,evidence=?,status=?,error=?,updated_at=?
            WHERE id=?""", (text[-200_000:], json.dumps(evidence[-100:]), status,
                           error[:4000], time.time(), turn_id))


def get(turn_id):
    ensure_db()
    with longterm._conn() as db:
        row = db.execute("SELECT * FROM companion_jobs WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            return None
        keys = [c[1] for c in db.execute('PRAGMA table_info(companion_jobs)')]
    data = dict(zip(keys, row))
    data['evidence'] = json.loads(data['evidence'])
    return data


def recent():
    ensure_db()
    with longterm._conn() as db:
        rows = db.execute("""SELECT id,thread_id,message,status,updated_at
            FROM companion_jobs ORDER BY created_at DESC LIMIT 30""").fetchall()
    return [dict(zip(('id', 'thread_id', 'message', 'status', 'updated_at'), r)) for r in rows]
