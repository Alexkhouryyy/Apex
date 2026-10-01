"""World model — synthesizes goals, awareness events, and entities into a live context snapshot.

Built every 5 minutes from the _review_loop heartbeat. Stored in the `world_state`
SQLite table (key/value) and injected into the system prompt alongside
active_goals_for_prompt() so every response is grounded in current reality.
"""
import time
from typing import Optional

import config
from agent import longterm, telemetry

_WORLD_MODEL_INTERVAL = 300.0  # rebuild at most every 5 min
_last_build: float = 0.0
_cached_state: str = ""


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS world_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)


def get(key: str = "current") -> str:
    """Return the cached world state string for the given key."""
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT value FROM world_state WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else ""
    except Exception:
        return ""


# The paragraph says what the user is doing "right now". After Apex has been
# off (a night, a trip), that is last week's guess presented as the present,
# so the prompt only carries it while the 5-minute rebuild is keeping it live.
FRESH_FOR = 1800.0


def get_fresh(key: str = "current", max_age: float = FRESH_FOR) -> str:
    """The world state, or "" when it was last rebuilt more than max_age ago."""
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT value, updated_at FROM world_state WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row and time.time() - row[1] <= max_age else ""
    except Exception:
        return ""


def _put(key: str, value: str) -> None:
    now = time.time()
    with longterm._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO world_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )


def build(client, log, force: bool = False) -> str:
    """Rebuild the world state from current context. Returns the new state string.

    Skips the rebuild if called within _WORLD_MODEL_INTERVAL unless force=True.
    """
    global _last_build, _cached_state
    now = time.time()
    if not force and now - _last_build < _WORLD_MODEL_INTERVAL:
        return _cached_state
    _last_build = now

    from agent import goals as goals_mod

    active_goals = goals_mod.list_goals(active_only=True)
    goals_text = "\n".join(
        f"- [{g['horizon']}] {g['title']}" for g in active_goals[:6]
    ) or "none"

    recent_events = log.recent(since_seconds=1800)  # last 30 min
    events_text = "\n".join(
        f"[{e['source']}] {e['content']}" for e in recent_events[-20:]
    ) or "none"

    try:
        with longterm._conn() as c:
            ents = c.execute(
                "SELECT name, kind FROM entities ORDER BY importance DESC, last_seen DESC LIMIT 10"
            ).fetchall()
        entities_text = ", ".join(f"{e[0]} ({e[1]})" for e in ents) or "none"
    except Exception:
        entities_text = "none"

    prompt = (
        "You are maintaining a live context model for an AI agent.\n\n"
        f"ACTIVE GOALS:\n{goals_text}\n\n"
        f"RECENT ACTIVITY (last 30 min):\n{events_text}\n\n"
        f"KEY ENTITIES: {entities_text}\n\n"
        "Write a 2-3 sentence 'current state' paragraph that captures what the user "
        "is likely working on right now, what matters most, and any relevant context. "
        "Be concrete and specific. No filler. If there is no meaningful activity, "
        "say so in one sentence."
    )

    try:
        resp = telemetry.create(
            client,
            call_site="agent.world_model/build",
            model=config.PROACTIVE_MODEL,
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        state = resp.content[0].text.strip()
    except Exception as e:
        print(f"[WorldModel] Build failed: {e}")
        return _cached_state

    if state:
        _cached_state = state
        try:
            _put("current", state)
        except Exception:
            pass
    return _cached_state
