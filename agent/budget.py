"""Spend-cap safety rails — daily and per-session budget limits.

Limits are stored in the agent DB (budget_config table) so they can be
updated from the dashboard at runtime without a restart.
"""
import time

from agent import longterm

_DEFAULTS = {
    "daily_usd":   "5.00",
    "session_usd": "2.00",
    "enabled":     "true",
}


# WHICH database was initialised, not merely THAT one was.
#
# This was a bare `_initialized = False` boolean, and the difference is not
# academic: `_ensure_init()` skips creating the table whenever the flag is set,
# so once anything had called `init_db()` against one database, the lazy guard
# stopped guarding for every other. A test that pointed longterm.DB_PATH at a
# temp file was enough to leave `budget_config` permanently uncreated in the
# real one — observed, as a failure in a completely unrelated test file that
# ran afterwards.
#
# The same shape would bite outside tests: restoring from a backup, or any
# runtime DB_PATH change, would silently disarm a guard whose whole job is to
# make missing tables impossible.
_initialized_for: str | None = None


def init_db() -> None:
    global _initialized_for
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS budget_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        for k, v in _DEFAULTS.items():
            c.execute(
                "INSERT OR IGNORE INTO budget_config (key, value) VALUES (?, ?)",
                (k, v),
            )
        c.commit()
    _initialized_for = str(longterm.DB_PATH)


def _ensure_init() -> None:
    """Lazily create the table so callers (tests, partial boots) never hit a
    missing-table error if init_db() wasn't run during startup."""
    if _initialized_for != str(longterm.DB_PATH):
        init_db()


def get_config() -> dict:
    _ensure_init()
    with longterm._conn() as c:
        rows = c.execute("SELECT key, value FROM budget_config").fetchall()
    cfg = {r[0]: r[1] for r in rows}
    return {
        "daily_usd":   float(cfg.get("daily_usd",   _DEFAULTS["daily_usd"])),
        "session_usd": float(cfg.get("session_usd", _DEFAULTS["session_usd"])),
        "enabled":     cfg.get("enabled", _DEFAULTS["enabled"]).lower() == "true",
    }


def set_config(updates: dict) -> None:
    _ensure_init()
    with longterm._conn() as c:
        for k, v in updates.items():
            c.execute(
                "INSERT OR REPLACE INTO budget_config (key, value) VALUES (?, ?)",
                (k, str(v)),
            )
        c.commit()


def today_spend() -> float:
    day_start = (time.time() // 86400) * 86400
    with longterm._conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE ts >= ?",
            (day_start,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def session_spend() -> float:
    from agent import telemetry
    sid = telemetry._session_id
    if sid is None:
        return 0.0
    with longterm._conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE session_id = ?",
            (sid,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def check() -> "str | None":
    """Return an error string if a spend limit is exceeded, None if OK."""
    cfg = get_config()
    if not cfg["enabled"]:
        return None

    daily_limit = cfg["daily_usd"]
    if daily_limit > 0:
        spent = today_spend()
        if spent >= daily_limit:
            return (
                f"[Safety] Daily spend cap ${daily_limit:.2f} reached "
                f"(${spent:.2f} used today). Apex is paused until midnight UTC."
            )

    session_limit = cfg["session_usd"]
    if session_limit > 0:
        spent = session_spend()
        if spent >= session_limit:
            return (
                f"[Safety] Session spend cap ${session_limit:.2f} reached "
                f"(${spent:.2f} this session). Start a new session to continue."
            )

    return None
