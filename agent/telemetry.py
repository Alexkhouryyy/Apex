"""Token usage, cost, latency telemetry for shared provider calls.

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
import math
from contextlib import contextmanager
from typing import Optional

import config
from agent import longterm
from agent.incident_replay import boundary
from agent import message_snapshot

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


def _pricing(model: str) -> dict | None:
    if model.startswith("ollama/"):
        return _ZERO
    prices = getattr(config, "MODEL_PRICING", {})
    if model in prices:
        try:
            p = {k: float(prices[model][k]) for k in _ZERO}
            if all(math.isfinite(v) and v >= 0 for v in p.values()):
                return p
        except (ValueError, TypeError, KeyError):
            pass
    if model not in _warned_unpriced:
        _warned_unpriced.add(model)
        print(f"[Cost] No price for '{model}' — its cost is unknown and the "
              f"budget cap requires pricing. Add it to MODEL_PRICING_JSON in .env, "
              f"e.g. {{\"{model}\": {{\"input\": 5.0, \"output\": 25.0}}}}")
    return None


def _usage_counts(usage):
    if usage is None:
        return None
    if any(getattr(usage, k, None) is None for k in ("input_tokens", "output_tokens")):
        return None
    values = {k: getattr(usage, k, 0)
              for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if values[key] is None:
            values[key] = 0
    if all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values.values()):
        return values
    return None


def _compute_cost(model: str, usage) -> float | None:
    p = _pricing(model)
    counts = _usage_counts(usage)
    if p is None or counts is None:
        return None
    inp = counts["input_tokens"]
    out = counts["output_tokens"]
    cr = counts["cache_read_input_tokens"]
    cc = counts["cache_creation_input_tokens"]
    try:
        cost = (
            inp * p["input"] / 1_000_000
            + out * p["output"] / 1_000_000
            + cr * p["cache_read"] / 1_000_000
            + cc * p["cache_create"] / 1_000_000
        )
    except OverflowError:
        return None
    return cost if math.isfinite(cost) else None


def record(
    *,
    call_site: str,
    model: str,
    usage,
    latency_ms: int,
    stop_reason: str = "",
    tool_calls: Optional[list] = None,
    reservation=None,
    incomplete: bool = False,
) -> bool:
    """Persist one provider call's usage. `usage` is the SDK's Usage object."""
    cost = _compute_cost(model, usage) if not incomplete else None
    counts = _usage_counts(usage) or {}
    cost_status = "usage"
    if cost is None:
        cost = reservation.amount if reservation is not None else None
        cost_status = "estimate" if cost is not None else "unknown"
    try:
        with longterm._write_conn() as c:
            longterm._ensure_usage_schema(c)
            c.execute(
                """INSERT INTO usage_log
                   (ts, session_id, turn_index, call_site, model,
                    input_tokens, cache_read_tokens, cache_creation_tokens,
                    output_tokens, thinking_tokens, latency_ms, cost_usd,
                    tool_calls_json, stop_reason, cost_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    reservation.session_id if reservation is not None else _session_id,
                    reservation.turn_index if reservation is not None else _turn_index,
                    call_site,
                    model,
                    counts.get("input_tokens", 0),
                    counts.get("cache_read_input_tokens", 0),
                    counts.get("cache_creation_input_tokens", 0),
                    counts.get("output_tokens", 0),
                    0,  # thinking tokens not exposed separately yet
                    latency_ms,
                    cost,
                    json.dumps(tool_calls or []),
                    stop_reason,
                    cost_status,
                ),
            )
            # Settlement and release share one transaction. A failed ledger
            # write leaves the reservation held, rather than refunding unknown spend.
            if reservation is not None:
                c.execute("DELETE FROM budget_reservations WHERE id = ?", (reservation.id,))
        return True
    except Exception as e:
        print(f"[Telemetry] record failed: {e}")
        return False


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


# Automatic helpers historically received a cached Anthropic client regardless
# of the configured model. Keep explicit main/council/model-comparison calls out
# of this list: choosing a council member must still mean choosing that model.
_BACKGROUND_SITES = {
    "agent.world_model/build", "agent.reflection/consolidate",
    "agent.reflection/refine_skill", "agent.reflection/profile_digest",
    "agent.memory/maybe_summarize", "agent.awareness/review",
    "agent.cortex/decide", "agent.skill_forge/forge", "agent.skill_forge/acquire",
    "agent.prefs/distill", "agent.goals/evaluate_recent_work",
    "agent.core/skill_propose", "agent.core/proactive_check",
    "agent.curator/dedup", "tools.screen_vision/describe",
    "deepresearch/plan", "deepresearch/queries", "deepresearch/extract",
    "deepresearch/gaps", "deepresearch/write",
}


def _resolve_call(client, call_site, kwargs):
    from agent import provider
    if call_site not in _BACKGROUND_SITES:
        return client, kwargs
    model = config.BACKGROUND_MODEL
    # Preserve existing injected clients and Haiku budgets in Anthropic mode.
    if provider.provider_for(model) == "anthropic":
        model = kwargs.get("model", model)
    kwargs = {**kwargs, "model": model}
    if provider.provider_for(model) != "anthropic":
        for key in ("thinking", "output_config", "temperature", "top_p", "top_k"):
            kwargs.pop(key, None)
        client = provider.get_client(model)
    return client, kwargs


@boundary("apex.llm_create", exclude_arguments=("client",),
          encode_result=message_snapshot.encode, decode_result=message_snapshot.decode)
def create(client, *, call_site: str, **kwargs):
    """Drop-in replacement for client.messages.create with telemetry capture."""
    client, kwargs = _resolve_call(client, call_site, kwargs)
    from agent import budget
    ticket = budget.reserve(kwargs)
    start = time.time()
    try:
        resp = client.messages.create(**kwargs)
    except BaseException:
        record(call_site=call_site, model=kwargs.get("model", "?"), usage=None,
               latency_ms=int((time.time() - start) * 1000), stop_reason="request_error", reservation=ticket)
        raise
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
        reservation=ticket,
    )
    return resp


class StreamUnavailable(RuntimeError):
    """Streaming is unsupported before dispatch; a blocking fallback is safe."""


class _TrackedStream:
    def __init__(self, source):
        self.source = source
        self.completed = False
        self.final = None
        self.final_attempted = False

    def __iter__(self):
        for event in self.source:
            yield event
        self.completed = True

    def get_final_message(self):
        if not self.final_attempted:
            self.final_attempted = True
            self.final = self.source.get_final_message()
            self.completed = True
        return self.final

    def __getattr__(self, name):
        return getattr(self.source, name)


@contextmanager
def stream(client, *, call_site: str, **kwargs):
    """Record streams and interruptions without draining a cancelled request."""
    from agent import budget
    client, kwargs = _resolve_call(client, call_site, kwargs)
    ticket = budget.reserve(kwargs)
    start = time.time()
    tracked = None
    final = None
    undispatched = False
    try:
        try:
            method = getattr(client.messages, "stream", None)
            if method is None:
                raise NotImplementedError("stream method is unavailable")
            sm = method(**kwargs)
        except NotImplementedError as exc:
            budget.release(ticket)
            undispatched = True
            raise StreamUnavailable(str(exc)) from exc
        with sm as source:
            tracked = _TrackedStream(source)
            try:
                yield tracked
            finally:
                # Only request a final message after exhaustion. Calling it on
                # an interrupted stream can drain or re-dispatch the request.
                if tracked.completed:
                    final = tracked.get_final_message()
    finally:
        if not undispatched:
            partial = None
            if final is None and tracked is not None:
                try:
                    partial = getattr(tracked.source, "current_message_snapshot", None)
                except Exception:
                    pass
            message = final if final is not None else partial
            record(call_site=call_site, model=kwargs.get("model", "?"),
                   usage=getattr(message, "usage", None),
                   latency_ms=int((time.time() - start) * 1000),
                   stop_reason=(getattr(final, "stop_reason", "") or "end_turn") if final is not None else "stream_interrupted",
                   tool_calls=[{"name": getattr(b, "name", ""), "id": getattr(b, "id", "")}
                               for b in getattr(message, "content", []) if getattr(b, "type", "") == "tool_use"],
                   reservation=ticket, incomplete=final is None)


# === Aggregation helpers (consumed by dashboard) ===

def summary(days: int = 7) -> dict:
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        rows = c.execute(
            """SELECT model, COUNT(*) as calls,
                      SUM(input_tokens), SUM(cache_read_tokens),
                      SUM(cache_creation_tokens), SUM(output_tokens),
                      SUM(cost_usd), AVG(latency_ms), COUNT(*) - COUNT(cost_usd),
                      SUM(CASE WHEN cost_status = 'estimate' THEN 1 ELSE 0 END)
               FROM usage_log WHERE ts >= ? GROUP BY model""",
            (cutoff,),
        ).fetchall()
        totals = c.execute(
            "SELECT COUNT(*), SUM(cost_usd), SUM(input_tokens), SUM(cache_read_tokens), SUM(output_tokens), "
            "COUNT(*) - COUNT(cost_usd), SUM(CASE WHEN cost_status = 'estimate' THEN 1 ELSE 0 END) "
            "FROM usage_log WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
        daily = c.execute(
            """SELECT CAST(ts/86400 AS INTEGER) * 86400 as day, SUM(cost_usd), COUNT(*), COUNT(*) - COUNT(cost_usd)
               FROM usage_log WHERE ts >= ? GROUP BY day ORDER BY day""",
            (cutoff,),
        ).fetchall()

    total_input = totals[2] or 0
    cache_read = totals[3] or 0
    hit_rate = (cache_read / (total_input + cache_read)) if (total_input + cache_read) > 0 else 0.0

    return {
        "days": days,
        "total_calls": totals[0] or 0,
        "total_cost_usd": None if totals[5] else round(totals[1] or 0.0, 4),
        "known_cost_usd": round(totals[1] or 0.0, 4),
        "unknown_cost_calls": totals[5] or 0,
        "estimated_cost_calls": totals[6] or 0,
        "total_input_tokens": total_input,
        "cache_read_tokens": cache_read,
        "total_output_tokens": totals[4] or 0,
        "cache_hit_rate": round(hit_rate, 3),
        "by_model": [
            {
                "model": r[0], "calls": r[1],
                "input_tokens": r[2] or 0, "cache_read_tokens": r[3] or 0,
                "cache_creation_tokens": r[4] or 0, "output_tokens": r[5] or 0,
                "cost_usd": None if r[8] else round(r[6] or 0.0, 4),
                "unknown_cost_calls": r[8], "estimated_cost_calls": r[9],
                "avg_latency_ms": int(r[7] or 0),
            }
            for r in rows
        ],
        "by_day": [{"day": int(d[0]), "cost_usd": None if d[3] else round(d[1] or 0.0, 4), "calls": d[2]} for d in daily],
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
            "output_tokens, latency_ms, cost_usd, stop_reason, cost_status FROM usage_log "
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
                "cost_status": u[10],
            }
            for u in usage
        ],
    }


def list_recent_sessions(limit: int = 20) -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            """SELECT s.id, s.started_at, s.ended_at, s.summary,
                      (SELECT SUM(cost_usd) FROM usage_log WHERE session_id = s.id),
                      (SELECT COUNT(*) FROM usage_log WHERE session_id = s.id),
                      (SELECT COUNT(*) - COUNT(cost_usd) FROM usage_log WHERE session_id = s.id)
               FROM sessions s ORDER BY s.started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0], "started_at": r[1], "ended_at": r[2],
            "summary": (r[3] or "")[:200],
            "cost_usd": None if r[6] else round(r[4] or 0.0, 4), "calls": r[5] or 0,
            "unknown_cost_calls": r[6],
        }
        for r in rows
    ]


def _safe_load(s: str):
    try:
        return json.loads(s)
    except Exception:
        return s
