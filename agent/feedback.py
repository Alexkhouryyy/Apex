"""User feedback on completed turns.

A turn is one user→assistant exchange identified by (session_id, turn_index).
Feedback is a thumbs-up (+1) or thumbs-down (-1) with an optional comment,
captured from the dashboard chat, the CLI (`/feedback +1`), or recognized
voice phrases ("thumbs up", "that was wrong").

This data is the raw signal the rest of the self-improvement loop (outcome
scoring, auto-rollback experiments, reflection re-ranking) will key off of.
"""
import time
from typing import Optional

from agent import longterm

_VALID_SOURCES = {"dashboard", "cli", "voice", "phone", "api"}


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS turn_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                session_id INTEGER NOT NULL,
                turn_index INTEGER NOT NULL,
                rating INTEGER NOT NULL,        -- +1 thumbs up, -1 thumbs down
                comment TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT 'dashboard',
                UNIQUE(session_id, turn_index)
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_ts ON turn_feedback(ts DESC)"
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_session ON turn_feedback(session_id, turn_index)"
        )


def record(
    rating: int,
    *,
    session_id: int,
    turn_index: int,
    comment: str = "",
    source: str = "dashboard",
) -> dict:
    """Upsert feedback for one turn. Re-rating overwrites the previous value."""
    if rating not in (1, -1):
        raise ValueError("rating must be +1 (thumbs up) or -1 (thumbs down)")
    if source not in _VALID_SOURCES:
        source = "api"

    with longterm._conn() as c:
        c.execute(
            """INSERT INTO turn_feedback (ts, session_id, turn_index, rating, comment, source)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id, turn_index) DO UPDATE SET
                   ts = excluded.ts,
                   rating = excluded.rating,
                   comment = excluded.comment,
                   source = excluded.source""",
            (time.time(), session_id, turn_index, rating, comment, source),
        )
        row = c.execute(
            "SELECT id, ts, rating, comment, source FROM turn_feedback "
            "WHERE session_id = ? AND turn_index = ?",
            (session_id, turn_index),
        ).fetchone()

    return {
        "id": row[0],
        "ts": row[1],
        "session_id": session_id,
        "turn_index": turn_index,
        "rating": row[2],
        "comment": row[3] or "",
        "source": row[4],
    }


def for_turn(session_id: int, turn_index: int) -> Optional[dict]:
    with longterm._conn() as c:
        row = c.execute(
            "SELECT id, ts, rating, comment, source FROM turn_feedback "
            "WHERE session_id = ? AND turn_index = ?",
            (session_id, turn_index),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "ts": row[1], "session_id": session_id,
        "turn_index": turn_index, "rating": row[2],
        "comment": row[3] or "", "source": row[4],
    }


def for_session(session_id: int) -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, ts, turn_index, rating, comment, source FROM turn_feedback "
            "WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "ts": r[1], "session_id": session_id,
            "turn_index": r[2], "rating": r[3],
            "comment": r[4] or "", "source": r[5],
        }
        for r in rows
    ]


def recent(limit: int = 50, days: int = 30) -> list[dict]:
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, ts, session_id, turn_index, rating, comment, source "
            "FROM turn_feedback WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [
        {
            "id": r[0], "ts": r[1], "session_id": r[2],
            "turn_index": r[3], "rating": r[4],
            "comment": r[5] or "", "source": r[6],
        }
        for r in rows
    ]


def summary(days: int = 7) -> dict:
    """Aggregate counts + ratio over a window. Includes per-day breakdown."""
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        totals = c.execute(
            "SELECT "
            "  SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END), "
            "  COUNT(*) "
            "FROM turn_feedback WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
        by_source = c.execute(
            "SELECT source, "
            "  SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) "
            "FROM turn_feedback WHERE ts >= ? GROUP BY source",
            (cutoff,),
        ).fetchall()
        daily = c.execute(
            "SELECT CAST(ts/86400 AS INTEGER) * 86400 as day, "
            "  SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END), "
            "  SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) "
            "FROM turn_feedback WHERE ts >= ? GROUP BY day ORDER BY day",
            (cutoff,),
        ).fetchall()

    ups = totals[0] or 0
    downs = totals[1] or 0
    total = totals[2] or 0
    ratio = (ups / total) if total else None
    return {
        "days": days,
        "thumbs_up": ups,
        "thumbs_down": downs,
        "total": total,
        "approval_rate": round(ratio, 3) if ratio is not None else None,
        "by_source": [
            {"source": r[0], "thumbs_up": r[1] or 0, "thumbs_down": r[2] or 0}
            for r in by_source
        ],
        "by_day": [
            {"day": int(r[0]), "thumbs_up": r[1] or 0, "thumbs_down": r[2] or 0}
            for r in daily
        ],
    }


# === Voice phrase recognition ===
_THUMBS_UP_PHRASES = (
    "thumbs up", "thumbs-up", "thumb up",
    "good job", "good answer", "well done", "nice work",
    "that was helpful", "that was great", "that worked",
    "perfect", "exactly right",
)
_THUMBS_DOWN_PHRASES = (
    "thumbs down", "thumbs-down", "thumb down",
    "bad answer", "wrong answer", "that was wrong", "that's wrong",
    "not helpful", "that was useless", "that didn't work",
    "that's incorrect", "you're wrong",
)


def detect_feedback_phrase(text: str) -> Optional[int]:
    """Return +1, -1, or None — no need to send a normal turn for pure feedback."""
    low = text.lower().strip().rstrip(".!?")
    # Only treat the message as pure feedback if it's short — otherwise it's a
    # follow-up question that happens to contain "thanks, that was helpful, now …".
    if len(low.split()) > 8:
        return None
    if any(p in low for p in _THUMBS_DOWN_PHRASES):
        return -1
    if any(p in low for p in _THUMBS_UP_PHRASES):
        return 1
    return None


def capture_phrase(text: str, *, session_id: Optional[int], last_turn: int,
                   source: str = "voice") -> Optional[int]:
    """Record `text` as feedback on `last_turn` if it is a feedback phrase.

    Returns the rating if it was captured (caller should then NOT run a normal
    agent turn), or None if this is ordinary input that should proceed.

    `last_turn` must be the turn index CAPTURED when that answer completed — not
    a live read of `telemetry.current_turn()`. The turn counter is a process-wide
    global that the scheduler, cortex, channels and dashboard all advance, so a
    live read can rate a background turn the user never saw.

    Never raises: feedback capture must not break a conversation.
    """
    try:
        rating = detect_feedback_phrase(text)
        if rating is None or session_id is None or last_turn < 1:
            return None
        record(rating, session_id=session_id, turn_index=last_turn,
               comment=text, source=source)
        return rating
    except Exception:
        return None
