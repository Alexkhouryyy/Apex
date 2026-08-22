"""Perception stream — everything Apex observes, persisted with FTS5 full-text search.

Supplements the in-memory AwarenessLog ring buffer with durable SQLite storage so
events survive restarts and are searchable over arbitrarily long time horizons.

Tools exposed to the agent: query_perception, recall_at_time.
"""
import time
from typing import Optional

from agent import longterm


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS perception_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_perc_ts ON perception_log(ts DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_perc_source ON perception_log(source, ts DESC)"
        )
        try:
            had_trigger = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='perception_ai'"
            ).fetchone()
            c.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS perception_fts
                USING fts5(content, content="perception_log", content_rowid="id")
            """)
            # An external-content FTS5 table is NOT populated by writes to its
            # content table — it needs triggers. There were none, so the index
            # stayed empty and `content MATCH ?` matched nothing. It did not
            # raise either, so the LIKE fallback below never ran: query() has
            # always returned [] and the query_perception tool has never found a
            # single event. Same pattern as turn_log_fts in agent/longterm.py.
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS perception_ai AFTER INSERT ON perception_log BEGIN
                  INSERT INTO perception_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS perception_ad AFTER DELETE ON perception_log BEGIN
                  INSERT INTO perception_fts(perception_fts, rowid, content) VALUES('delete', old.id, old.content);
                END
            """)
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS perception_au AFTER UPDATE ON perception_log BEGIN
                  INSERT INTO perception_fts(perception_fts, rowid, content) VALUES('delete', old.id, old.content);
                  INSERT INTO perception_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)
            if not had_trigger:
                # Backfill everything already logged — on an existing database
                # that is every event Apex has ever observed.
                c.execute("INSERT INTO perception_fts(perception_fts) VALUES('rebuild')")
        except Exception:
            pass  # FTS5 unavailable — query() falls back to LIKE


def log_event(source: str, content: str, ts: Optional[float] = None) -> None:
    """Persist one observed event. Safe to call from any watcher thread."""
    t = ts or time.time()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO perception_log (ts, source, content) VALUES (?, ?, ?)",
                (t, source, content),
            )
    except Exception as e:
        print(f"[Perception] log_event failed: {e}")


def query(query_text: str, since_hours: float = 24.0, limit: int = 20) -> list[dict]:
    """FTS5 search + time filter. Falls back to LIKE if FTS5 is unavailable."""
    cutoff = time.time() - since_hours * 3600
    try:
        with longterm._conn() as c:
            try:
                rows = c.execute(
                    """SELECT p.id, p.ts, p.source, p.content
                       FROM perception_fts fts
                       JOIN perception_log p ON p.id = fts.rowid
                       WHERE fts.content MATCH ?
                         AND p.ts >= ?
                       ORDER BY p.ts DESC LIMIT ?""",
                    (query_text, cutoff, limit),
                ).fetchall()
            except Exception:
                rows = c.execute(
                    """SELECT id, ts, source, content FROM perception_log
                       WHERE content LIKE ? AND ts >= ?
                       ORDER BY ts DESC LIMIT ?""",
                    (f"%{query_text}%", cutoff, limit),
                ).fetchall()
    except Exception as e:
        print(f"[Perception] query failed: {e}")
        return []
    return [{"id": r[0], "ts": r[1], "source": r[2], "content": r[3]} for r in rows]


def recall_at(time_iso: str, window_minutes: int = 10) -> list[dict]:
    """Return events perceived within ±window_minutes of the given ISO timestamp."""
    from datetime import datetime
    try:
        center = datetime.fromisoformat(time_iso).timestamp()
    except Exception:
        return []
    half = window_minutes * 30  # seconds in half the window
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, ts, source, content FROM perception_log "
                "WHERE ts BETWEEN ? AND ? ORDER BY ts ASC",
                (center - half, center + half),
            ).fetchall()
    except Exception:
        return []
    return [{"id": r[0], "ts": r[1], "source": r[2], "content": r[3]} for r in rows]


def recent(since_hours: float = 1.0, source: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Return recent events, optionally filtered by source."""
    cutoff = time.time() - since_hours * 3600
    try:
        with longterm._conn() as c:
            if source:
                rows = c.execute(
                    "SELECT id, ts, source, content FROM perception_log "
                    "WHERE ts >= ? AND source = ? ORDER BY ts DESC LIMIT ?",
                    (cutoff, source, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT id, ts, source, content FROM perception_log "
                    "WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
    except Exception:
        return []
    return [{"id": r[0], "ts": r[1], "source": r[2], "content": r[3]} for r in rows]
