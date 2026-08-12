"""Trajectory capture — per-tool-call outcome signals.

Apex logs a 400-char preview of each tool result, which is fine for replay and
useless for learning. This module records the four signals that actually matter
for improving an agent: did the call ERROR, did the agent RECOVER afterwards,
did it HALLUCINATE a tool that doesn't exist, and how long it took.

Why now, before the learning loop exists: these signals cannot be reconstructed
after the fact. A session that ran uninstrumented is data lost permanently. So
capture is deliberately decoupled from — and shipped ahead of — any consumer.

Design rules:
  - Capture must NEVER break a tool call. Every write is best-effort.
  - Store no secrets: we keep the tool name and a redacted shape of the inputs,
    never the values.
"""
from __future__ import annotations

import json
import time
from typing import Optional

from agent import longterm

# Outcome taxonomy — deliberately coarse so it stays stable enough to learn from.
OK = "ok"
ERROR = "error"
BLOCKED = "blocked"          # refused by the safety layer
UNKNOWN_TOOL = "unknown_tool"  # the model named a tool that does not exist

_RECOVERY_WINDOW_S = 120.0   # a later success on the same tool within this window counts as recovery


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            REAL NOT NULL,
                session_id    INTEGER,
                tool          TEXT NOT NULL,
                outcome       TEXT NOT NULL,
                error_kind    TEXT DEFAULT '',
                duration_ms   INTEGER DEFAULT 0,
                recovered     INTEGER NOT NULL DEFAULT 0,
                input_keys    TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_ts ON tool_events(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_tool ON tool_events(tool, ts DESC)")


def classify(result: str) -> tuple[str, str]:
    """Map a tool's return string to (outcome, error_kind).

    Relies on the sentinel strings core._execute_tool already returns, so this
    needs no changes at the ~100 individual tool handlers.
    """
    r = (result or "").lstrip()
    if r.startswith("Unknown tool:"):
        return UNKNOWN_TOOL, "no_such_tool"
    if r.startswith("[BLOCKED by safety layer]"):
        return BLOCKED, "safety_gate"
    # Common error shapes across Apex's tool handlers.
    low = r[:200].lower()
    if r.startswith("[") and "failed" in low:
        return ERROR, "handler_failed"
    for marker, kind in (
        ("traceback (most recent call last)", "exception"),
        ("timed out", "timeout"),
        ("not configured", "unconfigured"),
        ("not found", "not_found"),
        ("permission denied", "permission"),
        ("connection", "network"),
    ):
        if marker in low:
            return ERROR, kind
    return OK, ""


def _redact_keys(inputs: dict) -> str:
    """Record the SHAPE of the inputs (key names only) — never the values."""
    try:
        return ",".join(sorted(str(k) for k in (inputs or {}).keys()))[:200]
    except Exception:
        return ""


def record(tool: str, result: str, duration_ms: int = 0, inputs: Optional[dict] = None) -> str:
    """Record one tool call's outcome. Best-effort: never raises.

    Returns the outcome string (useful for callers/tests); '' if capture failed.
    """
    try:
        outcome, error_kind = classify(result)
        now = time.time()
        try:
            from agent import telemetry as _tel
            session_id = _tel._session_id
        except Exception:
            session_id = None
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO tool_events (ts, session_id, tool, outcome, error_kind, duration_ms, "
                "recovered, input_keys) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (now, session_id, tool, outcome, error_kind, int(duration_ms),
                 _redact_keys(inputs)),
            )
            # Recovery: this success closes out recent failures of the same tool.
            if outcome == OK:
                c.execute(
                    "UPDATE tool_events SET recovered = 1 "
                    "WHERE tool = ? AND outcome != ? AND recovered = 0 AND ts >= ?",
                    (tool, OK, now - _RECOVERY_WINDOW_S),
                )
        return outcome
    except Exception:
        return ""


def stats(days: int = 7) -> dict:
    """Aggregate reliability signals — the learning loop's first consumer."""
    cutoff = time.time() - days * 86400
    try:
        with longterm._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM tool_events WHERE ts >= ?", (cutoff,)).fetchone()[0]
            by_outcome = dict(c.execute(
                "SELECT outcome, COUNT(*) FROM tool_events WHERE ts >= ? GROUP BY outcome", (cutoff,)
            ).fetchall())
            unrecovered = c.execute(
                "SELECT COUNT(*) FROM tool_events WHERE ts >= ? AND outcome != ? AND recovered = 0",
                (cutoff, OK),
            ).fetchone()[0]
            worst = c.execute(
                "SELECT tool, COUNT(*) AS fails FROM tool_events "
                "WHERE ts >= ? AND outcome != ? GROUP BY tool ORDER BY fails DESC LIMIT 5",
                (cutoff, OK),
            ).fetchall()
    except Exception:
        return {"total": 0, "by_outcome": {}, "unrecovered": 0, "worst_tools": [], "success_rate": 0}
    ok = by_outcome.get(OK, 0)
    return {
        "total": total,
        "by_outcome": by_outcome,
        "unrecovered": unrecovered,
        "worst_tools": [{"tool": t, "failures": n} for t, n in worst],
        "success_rate": round(100 * ok / total) if total else 0,
    }


def recent(limit: int = 50) -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT ts, tool, outcome, error_kind, duration_ms, recovered "
                "FROM tool_events ORDER BY ts DESC LIMIT ?", (limit,),
            ).fetchall()
    except Exception:
        return []
    return [
        {"ts": r[0], "tool": r[1], "outcome": r[2], "error_kind": r[3],
         "duration_ms": r[4], "recovered": bool(r[5])}
        for r in rows
    ]
