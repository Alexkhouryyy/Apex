"""Spend-cap safety rails — daily and per-session budget limits.

Limits are stored in the agent DB (budget_config table) so they can be
updated from the dashboard at runtime without a restart.
"""
import time
import json
import math
from dataclasses import dataclass
from uuid import uuid4

from agent import longterm


class BudgetExceeded(RuntimeError):
    """A request cannot fit within the configured spend allowance."""


class BudgetUnavailable(RuntimeError):
    """Spending cannot be verified; do not dispatch a paid request."""


@dataclass(frozen=True)
class Reservation:
    id: str
    amount: float | None
    session_id: int | None
    turn_index: int

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
    with longterm._write_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        longterm._ensure_usage_schema(c)
        c.execute("""
            CREATE TABLE IF NOT EXISTS budget_reservations (
                id TEXT PRIMARY KEY,
                ts REAL NOT NULL,
                session_id INTEGER,
                model TEXT NOT NULL,
                amount REAL
            )
        """)
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
    updates = dict(updates)
    for key in ("daily_usd", "session_usd"):
        if key in updates:
            value = float(updates[key])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be a finite, non-negative amount.")
            updates[key] = value
    if "enabled" in updates:
        value = str(updates["enabled"]).lower()
        if value not in {"true", "false"}:
            raise ValueError("enabled must be true or false.")
        updates["enabled"] = value
    _ensure_init()
    with longterm._conn() as c:
        for k, v in updates.items():
            c.execute(
                "INSERT OR REPLACE INTO budget_config (key, value) VALUES (?, ?)",
                (k, str(v)),
            )
        c.commit()


def today_spend() -> float:
    _ensure_init()
    day_start = (time.time() // 86400) * 86400
    with longterm._conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE ts >= ?",
            (day_start,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def session_spend() -> float:
    _ensure_init()
    from agent import telemetry
    sid = telemetry._session_id
    with longterm._conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_log WHERE session_id IS ?",
            (sid,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def _totals(c, session_id):
    day_start = (time.time() // 86400) * 86400
    daily = c.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) - COUNT(cost_usd) FROM usage_log WHERE ts >= ?",
        (day_start,),
    ).fetchone()
    session = c.execute(
        "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) - COUNT(cost_usd) FROM usage_log WHERE session_id IS ?",
        (session_id,),
    ).fetchone()
    # Unfinished requests can cross midnight or survive a crash. Keep their
    # allowance held until accounting is resolved; never expire it as free spend.
    reserved, missing = c.execute("SELECT COALESCE(SUM(amount), 0), COUNT(*) - COUNT(amount) FROM budget_reservations").fetchone()
    session_reserved, session_missing = c.execute(
        "SELECT COALESCE(SUM(amount), 0), COUNT(*) - COUNT(amount) FROM budget_reservations WHERE session_id IS ?",
        (session_id,),
    ).fetchone()
    return (daily[0], daily[1] + missing), (session[0], session[1] + session_missing), float(reserved), float(session_reserved)


def reservations() -> dict:
    from agent import telemetry
    _ensure_init()
    with longterm._conn() as c:
        daily, session, held, session_held = _totals(c, telemetry._session_id)
    return {"daily_reserved": held, "session_reserved": session_held,
            "daily_unknown_calls": daily[1], "session_unknown_calls": session[1]}


def _limit_error(cfg, totals, amount=0.0):
    daily, session, held, session_held = totals
    for label, limit, spent, unknown, reserved in (
        ("Daily", cfg["daily_usd"], daily[0], daily[1], held),
        ("Session", cfg["session_usd"], session[0], session[1], session_held),
    ):
        if not math.isfinite(limit) or limit < 0:
            return f"[Safety] Invalid {label.lower()} spend cap; correct the budget settings."
        if limit <= 0:
            continue
        if unknown:
            return f"[Safety] {label} spend cannot be verified: {unknown} call(s) have unknown cost."
        if spent + reserved >= limit or spent + reserved + amount > limit:
            return (f"[Safety] {label} spend cap ${limit:.2f} reached or insufficient for this request "
                    f"(${spent:.4f} recorded, ${reserved:.4f} reserved, ${amount:.4f} requested).")
    return None


def check() -> "str | None":
    """Return an error string if a spend limit is exceeded, None if OK."""
    cfg = get_config()
    if not cfg["enabled"]:
        return None

    from agent import telemetry
    with longterm._conn() as c:
        return _limit_error(cfg, _totals(c, telemetry._session_id))


def reserve(kwargs: dict) -> Reservation:
    """Atomically reserve an estimated request cost before provider dispatch.

    UTF-8 request bytes conservatively estimate text input tokens, including
    tool schemas; reserve the full requested output allowance. These are token
    price estimates, not a provider invoice or a guarantee against price changes.
    """
    from agent import telemetry
    _ensure_init()
    model = kwargs.get("model", "?")
    prices = telemetry._pricing(model)
    amount = None
    if prices is not None:
        output = kwargs.get("max_tokens", 4096)
        if isinstance(output, bool) or not isinstance(output, int) or output <= 0:
            raise ValueError("max_tokens must be a positive integer.")
        input_bytes = len(json.dumps({k: kwargs[k] for k in ("system", "messages", "tools") if k in kwargs},
                                    ensure_ascii=False, default=str).encode("utf-8")) + 1024
        input_rate = max(prices[k] for k in ("input", "cache_read", "cache_create"))
        amount = (input_bytes * input_rate + output * prices["output"]) / 1_000_000
        if not math.isfinite(amount):
            raise BudgetUnavailable(f"[Safety] Invalid cost estimate for {model}; correct its pricing.")
    ticket = Reservation(uuid4().hex, amount, telemetry._session_id, telemetry._turn_index)
    with longterm._write_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        cfg = {k: v for k, v in c.execute("SELECT key, value FROM budget_config")}
        cfg = {"enabled": cfg["enabled"].lower() == "true",
               "daily_usd": float(cfg["daily_usd"]), "session_usd": float(cfg["session_usd"])}
        if cfg["enabled"] and (cfg["daily_usd"] != 0 or cfg["session_usd"] != 0):
            if amount is None:
                raise BudgetUnavailable(f"[Safety] No valid price for {model}; set MODEL_PRICING_JSON before using spend caps.")
            # Free models can continue even when paid calls are paused.
            if amount > 0:
                error = _limit_error(cfg, _totals(c, ticket.session_id), amount)
                if error:
                    raise BudgetExceeded(error)
        c.execute("INSERT INTO budget_reservations (id, ts, session_id, model, amount) VALUES (?, ?, ?, ?, ?)",
                  (ticket.id, time.time(), ticket.session_id, model, amount))
    return ticket


def release(ticket: Reservation) -> None:
    """Release only a request known not to have been dispatched."""
    with longterm._write_conn() as c:
        c.execute("DELETE FROM budget_reservations WHERE id = ?", (ticket.id,))
