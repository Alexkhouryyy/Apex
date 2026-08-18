"""Token usage, cost, latency telemetry for every Claude API call.

Wraps `client.messages.create(...)` and `client.messages.stream(...)` to capture
`resp.usage`, compute cost from MODEL_PRICING, persist a row to `usage_log`, and
return the response unchanged. Also stores per-turn data into `turn_log` for replay.

Usage:
    from agent import telemetry
    resp = telemetry.create(client, call_site="agent.core/main", **kwargs)
    with telemetry.stream(client, call_site="agent.core/stream", **kwargs) as stream:
        ...
"""
import json
import time
from contextlib import contextmanager
from typing import Optional

import config
from agent import longterm

# Ambient session/turn — main loop sets these so we don't have to thread them through.
_session_id: Optional[int] = None
_turn_index: int = 0


def set_session(session_id: int) -> None:
    global _session_id, _turn_index
    _session_id = session_id
    _turn_index = 0


def bump_turn() -> int:
    global _turn_index
    _turn_index += 1
    return _turn_index


def current_turn() -> int:
    return _turn_index


_ZERO = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_create": 0.0}
_warned_unpriced: set = set()


def _pricing(model: str) -> dict:
    prices = getattr(config, "MODEL_PRICING", {})
    if model in prices:
        return prices[model]
    # An unpriced model bills as $0, which does not merely mis-report a number —
    # it takes the budget cap offline, silently, for that model. Local models
    # really are free; anything else is a blind spot and has to say so. Once per
    # model per process: this sits on the hot path of every API call.
    if not model.startswith("ollama/") and model not in _warned_unpriced:
        _warned_unpriced.add(model)
        print(f"[Cost] No price for '{model}' — its spend counts as $0 and the "
              f"budget cap cannot see it. Add it to MODEL_PRICING_JSON in .env, "
              f"e.g. {{\"{model}\": {{\"input\": 5.0, \"output\": 25.0}}}}")
    return _ZERO


def _compute_cost(model: str, usage) -> float:
    p = _pricing(model)
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cc = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        inp * p["input"] / 1_000_000
        + out * p["output"] / 1_000_000
        + cr * p["cache_read"] / 1_000_000
        + cc * p["cache_create"] / 1_000_000
    )


def record(
    *,
    call_site: str,
    model: str,
    usage,
    latency_ms: int,
    stop_reason: str = "",
    tool_calls: Optional[list] = None,
) -> None:
    """Persist one Claude call's usage. `usage` is the SDK's Usage object."""
    if usage is None:
        return
    cost = _compute_cost(model, usage)
    try:
        with longterm._conn() as c:
            c.execute(
                """INSERT INTO usage_log
                   (ts, session_id, turn_index, call_site, model,
                    input_tokens, cache_read_tokens, cache_creation_tokens,
                    output_tokens, thinking_tokens, latency_ms, cost_usd,
                    tool_calls_json, stop_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    _session_id,
                    _turn_index,
                    call_site,
                    model,
                    getattr(usage, "input_tokens", 0) or 0,
                    getattr(usage, "cache_read_input_tokens", 0) or 0,
                    getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    getattr(usage, "output_tokens", 0) or 0,
                    0,  # thinking tokens not exposed separately yet
                    latency_ms,
                    cost,
                    json.dumps(tool_calls or []),
                    stop_reason,
                ),
            )
    except Exception as e:
        print(f"[Telemetry] record failed: {e}")


def log_turn(role: str, content_json: dict | list, tool_calls: Optional[list] = None) -> None:
    """Persist one chronological turn for episode replay."""
    try:
        with longterm._conn() as c:
            c.execute(
                """INSERT INTO turn_log (ts, session_id, turn_index, role, content_json, tool_calls_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    _session_id,
                    _turn_index,
                    role,
                    json.dumps(content_json, default=str),
                    json.dumps(tool_calls or [], default=str),
                ),
            )
    except Exception as e:
        print(f"[Telemetry] log_turn failed: {e}")


def create(client, *, call_site: str, **kwargs):
    """Drop-in replacement for client.messages.create with telemetry capture."""
    start = time.time()
    resp = client.messages.create(**kwargs)
    latency_ms = int((time.time() - start) * 1000)
    tool_calls = [
        {"name": getattr(b, "name", ""), "id": getattr(b, "id", "")}
        for b in getattr(resp, "content", [])
        if getattr(b, "type", "") == "tool_use"
    ]
    record(
        call_site=call_site,
        model=kwargs.get("model", "?"),
        usage=getattr(resp, "usage", None),
        latency_ms=latency_ms,
        stop_reason=getattr(resp, "stop_reason", "") or "",
        tool_calls=tool_calls,
    )
    return resp


@contextmanager
def stream(client, *, call_site: str, **kwargs):
    """Drop-in replacement for client.messages.stream that records the final usage."""
    start = time.time()
    sm = client.messages.stream(**kwargs)
    try:
        with sm as s:
            yield s
            final = s.get_final_message()
        latency_ms = int((time.time() - start) * 1000)
        tool_calls = [
            {"name": getattr(b, "name", ""), "id": getattr(b, "id", "")}
            for b in getattr(final, "content", [])
            if getattr(b, "type", "") == "tool_use"
        ]
        record(
            call_site=call_site,
            model=kwargs.get("model", "?"),
            usage=getattr(final, "usage", None),
            latency_ms=latency_ms,
            stop_reason=getattr(final, "stop_reason", "") or "",
            tool_calls=tool_calls,
        )
    except Exception:
        raise


# === Aggregation helpers (consumed by dashboard) ===

def summary(days: int = 7) -> dict:
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        rows = c.execute(
            """SELECT model, COUNT(*) as calls,
                      SUM(input_tokens), SUM(cache_read_tokens),
                      SUM(cache_creation_tokens), SUM(output_tokens),
                      SUM(cost_usd), AVG(latency_ms)
               FROM usage_log WHERE ts >= ? GROUP BY model""",
            (cutoff,),
        ).fetchall()
        totals = c.execute(
            "SELECT COUNT(*), SUM(cost_usd), SUM(input_tokens), SUM(cache_read_tokens), SUM(output_tokens) "
            "FROM usage_log WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
        daily = c.execute(
            """SELECT CAST(ts/86400 AS INTEGER) * 86400 as day, SUM(cost_usd), COUNT(*)
               FROM usage_log WHERE ts >= ? GROUP BY day ORDER BY day""",
            (cutoff,),
        ).fetchall()

    total_input = totals[2] or 0
    cache_read = totals[3] or 0
    hit_rate = (cache_read / (total_input + cache_read)) if (total_input + cache_read) > 0 else 0.0

    return {
        "days": days,
        "total_calls": totals[0] or 0,
        "total_cost_usd": round(totals[1] or 0.0, 4),
        "total_input_tokens": total_input,
        "cache_read_tokens": cache_read,
        "total_output_tokens": totals[4] or 0,
        "cache_hit_rate": round(hit_rate, 3),
        "by_model": [
            {
                "model": r[0], "calls": r[1],
                "input_tokens": r[2] or 0, "cache_read_tokens": r[3] or 0,
                "cache_creation_tokens": r[4] or 0, "output_tokens": r[5] or 0,
                "cost_usd": round(r[6] or 0.0, 4),
                "avg_latency_ms": int(r[7] or 0),
            }
            for r in rows
        ],
        "by_day": [{"day": int(d[0]), "cost_usd": round(d[1] or 0.0, 4), "calls": d[2]} for d in daily],
    }


def replay_session(session_id: int) -> dict:
    """Return chronological turns + usage rows for one session."""
    with longterm._conn() as c:
        turns = c.execute(
            "SELECT id, ts, turn_index, role, content_json, tool_calls_json FROM turn_log "
            "WHERE session_id = ? ORDER BY ts",
            (session_id,),
        ).fetchall()
        usage = c.execute(
            "SELECT ts, turn_index, call_site, model, input_tokens, cache_read_tokens, "
            "output_tokens, latency_ms, cost_usd, stop_reason FROM usage_log "
            "WHERE session_id = ? ORDER BY ts",
            (session_id,),
        ).fetchall()
        sess = c.execute(
            "SELECT id, started_at, ended_at, summary FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

    return {
        "session": {
            "id": sess[0] if sess else session_id,
            "started_at": sess[1] if sess else None,
            "ended_at": sess[2] if sess else None,
            "summary": sess[3] if sess else "",
        },
        "turns": [
            {
                "id": t[0], "ts": t[1], "turn_index": t[2], "role": t[3],
                "content": _safe_load(t[4]), "tool_calls": _safe_load(t[5]),
            }
            for t in turns
        ],
        "usage": [
            {
                "ts": u[0], "turn_index": u[1], "call_site": u[2], "model": u[3],
                "input_tokens": u[4], "cache_read_tokens": u[5], "output_tokens": u[6],
                "latency_ms": u[7], "cost_usd": u[8], "stop_reason": u[9],
            }
            for u in usage
        ],
    }


def list_recent_sessions(limit: int = 20) -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            """SELECT s.id, s.started_at, s.ended_at, s.summary,
                      (SELECT SUM(cost_usd) FROM usage_log WHERE session_id = s.id),
                      (SELECT COUNT(*) FROM usage_log WHERE session_id = s.id)
               FROM sessions s ORDER BY s.started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0], "started_at": r[1], "ended_at": r[2],
            "summary": (r[3] or "")[:200],
            "cost_usd": round(r[4] or 0.0, 4), "calls": r[5] or 0,
        }
        for r in rows
    ]


def _safe_load(s: str):
    try:
        return json.loads(s)
    except Exception:
        return s
