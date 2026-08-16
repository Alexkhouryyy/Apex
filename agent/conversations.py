"""Persistent chat threads for the dashboard.

Chat history lived in sessionStorage: close the tab and the conversation was
gone. Meanwhile every turn was already in SQLite — but not in a shape this can
use, which is why this is a separate store rather than a view over `turn_log`.

`turn_log.session_id` is **per process**: main.py and resident.py each call
`longterm.start_session()` once at boot. An always-on Apex therefore has one
session spanning weeks, so keying conversations off it would produce a single
endless thread. `channel_id` is passed to `agent.run` for per-channel memory
isolation and never reaches the database at all.

The two stores answer different questions and are kept apart deliberately:
turn_log is the agent's own record, for learning, telemetry and reranking;
this is what a person wants to scroll back through. Conflating them would give
one of them a bad shape.
"""
from __future__ import annotations

import time
from typing import Optional

from agent import longterm

# Titles are derived from the first thing you said, not generated. A model call
# per conversation to name it is spend for something a truncation does as well.
_TITLE_CHARS = 60
_DEFAULT_TITLE = "New conversation"

_ready = False


def _ensure_db() -> None:
    """Create tables on first use — the lesson from restraint, which shipped
    absent from main.py's init block and silently did nothing."""
    global _ready
    if _ready:
        return
    try:
        init_db()
        _ready = True
    except Exception:
        pass


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_threads (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id INTEGER NOT NULL,
                ts        REAL NOT NULL,
                role      TEXT NOT NULL,
                text      TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_thread "
                  "ON chat_messages(thread_id, ts)")


def create(title: str = "") -> int:
    _ensure_db()
    now = time.time()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO chat_threads (title, created_at, updated_at) VALUES (?,?,?)",
            (title or "", now, now))
        return int(cur.lastrowid)


def _title_from(text: str) -> str:
    one_line = " ".join((text or "").split())
    if not one_line:
        return _DEFAULT_TITLE
    return one_line[:_TITLE_CHARS] + ("…" if len(one_line) > _TITLE_CHARS else "")


def add_message(thread_id: int, role: str, text: str) -> None:
    """Append a message, titling the thread from the first thing you said."""
    _ensure_db()
    now = time.time()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO chat_messages (thread_id, ts, role, text) VALUES (?,?,?,?)",
                (int(thread_id), now, role, text or ""))
            c.execute("UPDATE chat_threads SET updated_at = ? WHERE id = ?",
                      (now, int(thread_id)))
            if role == "user":
                row = c.execute("SELECT title FROM chat_threads WHERE id = ?",
                                (int(thread_id),)).fetchone()
                if row is not None and not (row[0] or "").strip():
                    c.execute("UPDATE chat_threads SET title = ? WHERE id = ?",
                              (_title_from(text), int(thread_id)))
    except Exception as e:
        print(f"[Conversations] could not store message: {e}")


def list_threads(limit: int = 30) -> list[dict]:
    _ensure_db()
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT t.id, t.title, t.updated_at, COUNT(m.id) "
                "FROM chat_threads t LEFT JOIN chat_messages m ON m.thread_id = t.id "
                "GROUP BY t.id ORDER BY t.updated_at DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        return []
    # An empty thread is an accident of clicking New, not a conversation.
    return [{"id": r[0], "title": r[1] or _DEFAULT_TITLE,
             "updated_at": r[2], "messages": r[3]}
            for r in rows if r[3] > 0]


def messages(thread_id: int, limit: int = 500) -> list[dict]:
    _ensure_db()
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT role, text, ts FROM chat_messages WHERE thread_id = ? "
                "ORDER BY ts ASC, id ASC LIMIT ?", (int(thread_id), limit)).fetchall()
    except Exception:
        return []
    return [{"role": r[0], "text": r[1], "ts": r[2]} for r in rows]


def delete(thread_id: int) -> bool:
    _ensure_db()
    try:
        with longterm._conn() as c:
            c.execute("DELETE FROM chat_messages WHERE thread_id = ?", (int(thread_id),))
            c.execute("DELETE FROM chat_threads WHERE id = ?", (int(thread_id),))
        return True
    except Exception:
        return False


def latest_id() -> Optional[int]:
    """The most recently touched conversation, for reopening where you left off."""
    threads = list_threads(limit=1)
    return threads[0]["id"] if threads else None
