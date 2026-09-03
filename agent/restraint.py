"""Restraint — learning when *not* to interrupt.

Apex is always on, and everything it knows it can tell you: the guardian, the
time capsule, the scheduler, reflections, rollbacks, approvals. Every one of
those paths ends at `notify.Notifier.notify`, and none of them has ever asked
whether now is a good moment.

That matters more than it sounds. An assistant that interrupts badly gets muted,
and a muted assistant is worth exactly zero no matter how capable it is. The
scarce resource is not Apex's capability, it is your attention and your
willingness to keep listening — and every badly-timed notification is a
withdrawal from an account that, once empty, closes the whole product.

So this learns which moments you actually respond in, and holds the low-stakes
things until one of them. Four rules keep it from becoming a bug:

1. **Urgent always gets through.** Restraint gates convenience, never
   importance. A high-priority message is never held, whatever the model of
   your habits says.
2. **Held is not dropped.** Everything held is released — when you are clearly
   at the keyboard, when the hour turns receptive, or at the hold ceiling
   regardless. Silence that loses information is not restraint, it is a defect.
3. **Cold start is permissive.** With no evidence it sends everything. It learns
   to be quieter; it never begins quiet, because an agent that starts silent is
   indistinguishable from an agent that is broken.
4. **It fails open.** Any error anywhere in here results in the notification
   being sent. A bug in restraint must never be able to silence Apex.

Evidence, as with lessons: a moment earns its reputation from a real sample of
observed outcomes, and the outcome is measured, not guessed — did you actually
show up at the keyboard shortly after being pinged?
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

import config
from agent import longterm

# How long after a notification counts as "you engaged with it". Long enough to
# cover walking back to the desk; short enough that unrelated later activity is
# not miscredited.
ENGAGE_WINDOW_S = 15 * 60

# A moment needs this many scored observations before it is allowed an opinion.
# Below it, two ignored pings at 3pm should not silence every future 3pm.
MIN_SAMPLES = 8

# Engagement rate below which a moment is treated as a bad time.
QUIET_RATE = 0.25

# Nothing is ever held longer than this, whatever the model thinks.
MAX_HOLD_S = 6 * 3600


# WHICH database was initialised, not merely THAT one was. A bare boolean here
# records that some database has the tables and then skips the check for every
# other, so a DB_PATH change at runtime silently disarms the guard whose whole
# job is to make a missing table impossible. Found via agent/budget.py, which
# had the identical latch; see tests/test_schema.py::TestLazyGuardsArePerDatabase.
_ready_for: str | None = None


def _ensure_db() -> None:
    """Create tables on first use.

    Restraint sits on the notification path, which fires from schedulers, the
    awareness loop and channel handlers alike — so it cannot rely on any one
    entry point having initialised it. This module shipped without being in
    main.py's init block, and because every query fails open, the result was a
    feature that held nothing forever while appearing to work perfectly.
    """
    global _ready_for
    if _ready_for == str(longterm.DB_PATH):
        return
    try:
        init_db()
        _ready_for = str(longterm.DB_PATH)
    except Exception:
        pass                      # fail open: unreadable DB must not block a ping


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS interruptions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL NOT NULL,
                kind     TEXT DEFAULT '',
                priority TEXT DEFAULT 'normal',
                bucket   TEXT NOT NULL,
                engaged  INTEGER NOT NULL DEFAULT -1   -- -1 = not yet scored
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_interruptions_bucket "
                  "ON interruptions(bucket, engaged)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS held_notifications (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL,
                payload    TEXT NOT NULL,
                release_at REAL NOT NULL
            )
        """)


def enabled() -> bool:
    return bool(getattr(config, "RESTRAINT_ENABLED", True))


def bucket(ts: Optional[float] = None) -> str:
    """The moment, coarsely: weekday/weekend plus hour.

    Deliberately coarse. A finer grid would look more intelligent and learn far
    more slowly, and a model of your attention that needs a year of data to say
    anything is a model that never says anything.
    """
    dt = datetime.fromtimestamp(time.time() if ts is None else ts)
    day = "we" if dt.weekday() >= 5 else "wd"
    return f"{day}-{dt.hour:02d}"


# --- measuring what actually happened -----------------------------------------

def record(kind: str, priority: str = "normal", ts: Optional[float] = None) -> None:
    """Note that an interruption was delivered, for later scoring."""
    ts = time.time() if ts is None else ts
    _ensure_db()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO interruptions (ts, kind, priority, bucket, engaged) "
                "VALUES (?,?,?,?,-1)", (ts, kind or "", priority or "normal",
                                        bucket(ts)))
    except Exception:
        pass                      # never let bookkeeping break a notification


def score_pending(now: Optional[float] = None) -> int:
    """Decide, for interruptions old enough to judge, whether you engaged.

    Engagement is a user turn landing within ENGAGE_WINDOW_S of the ping. It is
    a proxy — you might have come to the keyboard for your own reasons — but it
    is measured rather than assumed, and it is the same signal a human would
    use to notice they are being ignored.
    """
    now = time.time() if now is None else now
    _ensure_db()
    scored = 0
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, ts FROM interruptions WHERE engaged = -1 AND ts <= ?",
                (now - ENGAGE_WINDOW_S,)).fetchall()
            for row_id, ts in rows:
                hit = c.execute(
                    "SELECT 1 FROM turn_log WHERE role = 'user' "
                    "AND ts > ? AND ts <= ? LIMIT 1",
                    (ts, ts + ENGAGE_WINDOW_S)).fetchone()
                c.execute("UPDATE interruptions SET engaged = ? WHERE id = ?",
                          (1 if hit else 0, row_id))
                scored += 1
    except Exception:
        return 0
    return scored


def receptiveness(b: Optional[str] = None) -> tuple[float, int]:
    """(engagement_rate, sample_size) for a moment. (1.0, 0) when unknown —
    an unknown moment is treated as a good one, per the cold-start rule."""
    b = bucket() if b is None else b
    _ensure_db()
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT COUNT(*), SUM(engaged) FROM interruptions "
                "WHERE bucket = ? AND engaged >= 0", (b,)).fetchone()
    except Exception:
        return 1.0, 0
    n = row[0] or 0
    if n <= 0:
        return 1.0, 0
    return (row[1] or 0) / n, n


# --- the decision --------------------------------------------------------------

def should_hold(kind: str = "", priority: str = "normal",
                now: Optional[float] = None) -> tuple[bool, str]:
    """Whether to hold this interruption, and why. Never raises."""
    try:
        if not enabled():
            return False, "restraint disabled"
        if (priority or "normal").lower() in ("high", "urgent", "critical"):
            return False, "urgent"          # rule 1: importance overrides habit
        b = bucket(now)
        rate, n = receptiveness(b)
        if n < MIN_SAMPLES:
            return False, f"only {n} samples for {b}"   # rule 3: cold = permissive
        if rate < QUIET_RATE:
            return True, f"{b} engagement {rate:.0%} over {n} pings"
        return False, f"{b} engagement {rate:.0%}"
    except Exception as e:
        return False, f"restraint error, sending anyway: {e}"   # rule 4


# --- holding, and never losing ------------------------------------------------

def hold(payload: dict, now: Optional[float] = None) -> None:
    """Park a notification. It will be released; it is never discarded."""
    import json
    now = time.time() if now is None else now
    _ensure_db()
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO held_notifications (ts, payload, release_at) "
                "VALUES (?,?,?)", (now, json.dumps(payload), now + MAX_HOLD_S))
    except Exception as e:
        print(f"[Restraint] could not hold notification ({e}); sending instead")
        raise


def due(now: Optional[float] = None, user_active: bool = False) -> list[dict]:
    """Held notifications ready to go out, removing them from the queue.

    Released when the hold ceiling passes, when the moment has become a
    receptive one, or when you are demonstrably at the keyboard — that last one
    being the whole point: the message arrives when you can actually take it.
    """
    import json
    now = time.time() if now is None else now
    _ensure_db()
    out: list[dict] = []
    try:
        rate, n = receptiveness(bucket(now))
        moment_ok = n < MIN_SAMPLES or rate >= QUIET_RATE
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, payload, release_at FROM held_notifications "
                "ORDER BY ts ASC").fetchall()
            for row_id, payload, release_at in rows:
                if not (user_active or moment_ok or now >= release_at):
                    continue
                try:
                    out.append(json.loads(payload))
                except Exception:
                    pass          # unreadable row: drop it, do not wedge the queue
                c.execute("DELETE FROM held_notifications WHERE id = ?", (row_id,))
    except Exception:
        return []
    return out


def held_count() -> int:
    try:
        with longterm._conn() as c:
            return c.execute("SELECT COUNT(*) FROM held_notifications").fetchone()[0]
    except Exception:
        return 0


def explain() -> str:
    """Why Apex is or is not talking right now — for the dashboard and for the
    question this feature has to be able to answer: 'why didn't you tell me?'"""
    b = bucket()
    rate, n = receptiveness(b)
    held = held_count()
    if n < MIN_SAMPLES:
        state = f"still learning ({n}/{MIN_SAMPLES} observations for {b})"
    elif rate < QUIET_RATE:
        state = f"holding non-urgent messages — you engage {rate:.0%} of the time at {b}"
    else:
        state = f"speaking freely — you engage {rate:.0%} of the time at {b}"
    return state + (f"; {held} message(s) waiting" if held else "")
