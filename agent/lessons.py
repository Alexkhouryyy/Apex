"""Procedural memory — learning what *works*, not just what happened.

Every consolidation path Apex had was episodic: reflection distils events,
prefs distils conversation, world_model distils state. All of them answer "what
happened". None answer "what works", even though `trajectory` records the
outcome of every single tool call and `verification` records every contract
pass and fail. Apex measured its own competence in fine detail and derived
nothing from it.

This closes that. The pipeline is deliberately asymmetric:

    statistics PROPOSE  →  the model ARTICULATES  →  statistics RETIRE

The model is allowed to phrase a lesson. It is never allowed to invent the
subject of one: every candidate originates in a measured pattern with a real
sample behind it, and the retirement check is pure SQL with no model in the
loop. A beautifully-worded wrong lesson therefore dies exactly as fast as an
ugly one.

**A lesson can never outlive its evidence.** That rule is the whole design.
Injecting remembered "lessons" into a system prompt is the standard way an
agent teaches itself superstition — it notices a coincidence, writes it down,
reads it back forever, and grows confidently wrong. Everything here exists to
make that impossible: each row stores the observations it came from, is
re-tested against fresh data on every pass, and is dropped the moment the
pattern stops holding.

Privacy comes for free: `tool_events.input_keys` holds key *names* only, never
values, so no lesson can leak the content of anything Apex touched.
"""
from __future__ import annotations

import time
from typing import Optional

import config
from agent import longterm

# A pattern needs this many observations before it is allowed to become a
# lesson. Below it, a run of bad luck looks identical to a real defect.
MIN_OBSERVATIONS = 5

# Failure rate at which a pattern is worth telling the agent about.
PROPOSE_RATE = 0.40

# Failure rate below which an existing lesson is retired. Deliberately lower
# than PROPOSE_RATE: without the gap a pattern hovering at the threshold would
# flap in and out on every pass.
RETIRE_RATE = 0.20

# A pattern nobody has exercised recently is not knowledge, it is a fossil.
STALE_DAYS = 30

# Hard cap on what reaches the prompt. Unbounded "lessons" is how a context
# window fills with folklore.
MAX_IN_PROMPT = 6

ACTIVE = "active"
RETIRED = "retired"


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                key            TEXT PRIMARY KEY,
                text           TEXT NOT NULL,
                scope          TEXT NOT NULL DEFAULT 'tool',
                evidence_n     INTEGER NOT NULL DEFAULT 0,
                evidence_rate  REAL NOT NULL DEFAULT 0.0,
                created_at     REAL NOT NULL,
                confirmed_at   REAL NOT NULL,
                status         TEXT NOT NULL DEFAULT 'active'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_lessons_status "
                  "ON lessons(status, evidence_n DESC)")


# --- 1. statistics propose ----------------------------------------------------

def observe(days: int = 14) -> list[dict]:
    """Measured failure patterns in the tool log. No model involved.

    Two shapes, both keyed so they can be re-measured later:
      tool:<name>|kind:<error_kind>   — this tool fails this way
      tool:<name>|shape:<input_keys>  — this tool fails on this call shape

    Returns only patterns that clear MIN_OBSERVATIONS and PROPOSE_RATE, so a
    candidate always has a real sample standing behind it.
    """
    since = time.time() - days * 86400
    out: list[dict] = []
    try:
        with longterm._conn() as c:
            totals = {
                row[0]: row[1] for row in c.execute(
                    "SELECT tool, COUNT(*) FROM tool_events WHERE ts >= ? "
                    "GROUP BY tool", (since,)).fetchall()
            }
            rows = c.execute(
                "SELECT tool, error_kind, COUNT(*) FROM tool_events "
                "WHERE ts >= ? AND outcome != 'ok' AND error_kind != '' "
                "GROUP BY tool, error_kind", (since,)).fetchall()
            for tool, kind, n_fail in rows:
                total = totals.get(tool, 0)
                if total < MIN_OBSERVATIONS:
                    continue
                rate = n_fail / total
                if rate >= PROPOSE_RATE:
                    out.append({"key": f"tool:{tool}|kind:{kind}", "scope": "tool",
                                "tool": tool, "detail": kind,
                                "n": total, "fails": n_fail, "rate": round(rate, 3)})

            # Shape patterns earn a slot only when they say something the
            # tool-level pattern does not. A tool called one way always, or
            # failing uniformly across every shape, is already fully described
            # by its error-kind lesson — emitting both spends two of six prompt
            # slots to state one fact twice.
            shapes_per_tool = {
                row[0]: row[1] for row in c.execute(
                    "SELECT tool, COUNT(DISTINCT input_keys) FROM tool_events "
                    "WHERE ts >= ? AND input_keys != '' GROUP BY tool",
                    (since,)).fetchall()
            }
            tool_fails = {
                row[0]: row[1] for row in c.execute(
                    "SELECT tool, COUNT(*) FROM tool_events "
                    "WHERE ts >= ? AND outcome != 'ok' GROUP BY tool",
                    (since,)).fetchall()
            }
            rows = c.execute(
                "SELECT tool, input_keys, COUNT(*) FROM tool_events "
                "WHERE ts >= ? AND outcome != 'ok' AND input_keys != '' "
                "GROUP BY tool, input_keys", (since,)).fetchall()
            for tool, shape, n_fail in rows:
                if shapes_per_tool.get(tool, 0) < 2:
                    continue                      # nothing to discriminate against
                shape_total = c_count(since, tool, shape)
                if shape_total < MIN_OBSERVATIONS:
                    continue
                rate = n_fail / shape_total
                overall = (tool_fails.get(tool, 0) / totals.get(tool, 1)
                           if totals.get(tool) else 0.0)
                # Meaningfully worse than the tool's own baseline, or it is just
                # the tool being broken restated per call shape.
                if rate >= PROPOSE_RATE and rate >= overall + 0.15:
                    out.append({"key": f"tool:{tool}|shape:{shape}", "scope": "shape",
                                "tool": tool, "detail": shape,
                                "n": shape_total, "fails": n_fail,
                                "rate": round(rate, 3)})
    except Exception as e:
        print(f"[Lessons] observe failed: {e}")
        return []
    out.sort(key=lambda p: (p["rate"], p["n"]), reverse=True)
    return out


def c_count(since: float, tool: str, shape: str) -> int:
    try:
        with longterm._conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM tool_events "
                "WHERE ts >= ? AND tool = ? AND input_keys = ?",
                (since, tool, shape)).fetchone()[0]
    except Exception:
        return 0


def measure(key: str, days: int = 14) -> Optional[dict]:
    """Re-measure one stored lesson's pattern against current data.

    This is what makes a lesson falsifiable: the key encodes exactly what was
    claimed, so the claim can be re-run. None means the pattern has no recent
    observations at all.
    """
    since = time.time() - days * 86400
    try:
        tool_part, _, detail_part = key.partition("|")
        tool = tool_part.split(":", 1)[1]
        field, _, detail = detail_part.partition(":")
        with longterm._conn() as c:
            if field == "kind":
                total = c.execute(
                    "SELECT COUNT(*) FROM tool_events WHERE ts >= ? AND tool = ?",
                    (since, tool)).fetchone()[0]
                fails = c.execute(
                    "SELECT COUNT(*) FROM tool_events WHERE ts >= ? AND tool = ? "
                    "AND outcome != 'ok' AND error_kind = ?",
                    (since, tool, detail)).fetchone()[0]
            else:
                total = c.execute(
                    "SELECT COUNT(*) FROM tool_events WHERE ts >= ? AND tool = ? "
                    "AND input_keys = ?", (since, tool, detail)).fetchone()[0]
                fails = c.execute(
                    "SELECT COUNT(*) FROM tool_events WHERE ts >= ? AND tool = ? "
                    "AND input_keys = ? AND outcome != 'ok'",
                    (since, tool, detail)).fetchone()[0]
    except Exception:
        return None
    if total <= 0:
        return None
    return {"n": total, "fails": fails, "rate": fails / total}


# --- 2. the model articulates --------------------------------------------------

_ARTICULATE_SYSTEM = (
    "You turn measured failure statistics into one short, actionable rule each.\n\n"
    "You are given real measurements from an agent's own tool log. For each, "
    "write ONE line of at most 18 words telling the agent what to do differently. "
    "Prefer a concrete alternative ('use bash curl instead') over a warning "
    "('be careful').\n\n"
    "Rules:\n"
    "- Describe only what the measurement shows. Do not speculate about causes "
    "you were not given.\n"
    "- Do not invent tools, errors or numbers.\n"
    "- No preamble, no numbering. Output exactly one line per input, in order."
)


def _articulate(client, candidates: list[dict]) -> list[str]:
    """Phrase measured patterns as advice. Falls back to a plain description.

    The model never chooses *what* is worth learning — only how to say it — so
    its worst failure mode is an awkward sentence, not a fabricated lesson.
    """
    # No counts here: for_prompt() appends the evidence, and duplicating it
    # reads as "failed 6/7 times (unconfigured) (6/7 recent calls)".
    plain = [
        (f"{c['tool']} often fails with {c['detail']}" if c["scope"] == "tool"
         else f"{c['tool']} often fails when called with {c['detail']}")
        for c in candidates
    ]
    if client is None or not candidates:
        return plain
    try:
        from agent import provider
        listing = "\n".join(
            f"- tool={c['tool']} problem={c['detail']} "
            f"failed={c['fails']}/{c['n']} rate={c['rate']:.0%}"
            for c in candidates)
        raw = provider.complete(
            config.PROACTIVE_MODEL, _ARTICULATE_SYSTEM, listing,
            max_tokens=400)
        lines = [ln.strip(" -•\t") for ln in raw.strip().splitlines() if ln.strip()]
        if len(lines) != len(candidates):
            return plain          # misaligned output: trust the measurements
        return [ln[:200] for ln in lines]
    except Exception as e:
        print(f"[Lessons] articulation failed ({e}); using plain descriptions")
        return plain


# --- 3. statistics retire ------------------------------------------------------

def retire(days: int = 14) -> int:
    """Drop lessons whose evidence no longer holds. No model in this path.

    Retirement is deliberately dumber than proposal. A lesson earns its place
    with a measurement and loses it with a measurement, so nothing survives on
    the strength of how convincing it sounds.
    """
    retired = 0
    now = time.time()
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT key, confirmed_at FROM lessons WHERE status = ?",
                (ACTIVE,)).fetchall()
        for key, confirmed_at in rows:
            fresh = measure(key, days=days)
            drop = False
            if fresh is None:
                # No observations at all — retire once it has also gone stale,
                # so a tool merely unused this fortnight is not forgotten.
                drop = (now - confirmed_at) > STALE_DAYS * 86400
            elif fresh["rate"] < RETIRE_RATE:
                drop = True
            if drop:
                with longterm._conn() as c:
                    c.execute("UPDATE lessons SET status = ? WHERE key = ?",
                              (RETIRED, key))
                retired += 1
            elif fresh is not None:
                with longterm._conn() as c:
                    c.execute(
                        "UPDATE lessons SET evidence_n = ?, evidence_rate = ?, "
                        "confirmed_at = ? WHERE key = ?",
                        (fresh["n"], round(fresh["rate"], 3), now, key))
    except Exception as e:
        print(f"[Lessons] retire failed: {e}")
    return retired


# --- the pass -----------------------------------------------------------------

def run(client=None, days: int = 14) -> dict:
    """One learning pass: retire what no longer holds, then learn what does."""
    init_db()
    retired = retire(days=days)

    candidates = observe(days=days)
    try:
        with longterm._conn() as c:
            known = {r[0] for r in c.execute("SELECT key FROM lessons").fetchall()}
    except Exception:
        known = set()

    fresh = [c for c in candidates if c["key"] not in known][:MAX_IN_PROMPT]
    texts = _articulate(client, fresh)

    now = time.time()
    learned = 0
    for cand, text in zip(fresh, texts):
        try:
            with longterm._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO lessons "
                    "(key, text, scope, evidence_n, evidence_rate, created_at, "
                    " confirmed_at, status) VALUES (?,?,?,?,?,?,?,?)",
                    (cand["key"], text, cand["scope"], cand["n"],
                     cand["rate"], now, now, ACTIVE))
            learned += 1
        except Exception as e:
            print(f"[Lessons] could not store lesson: {e}")

    # Reactivate a known pattern that has started failing again.
    revived = 0
    for cand in candidates:
        if cand["key"] not in known:
            continue
        try:
            with longterm._conn() as c:
                cur = c.execute("SELECT status FROM lessons WHERE key = ?",
                                (cand["key"],)).fetchone()
                if cur and cur[0] == RETIRED:
                    c.execute(
                        "UPDATE lessons SET status = ?, evidence_n = ?, "
                        "evidence_rate = ?, confirmed_at = ? WHERE key = ?",
                        (ACTIVE, cand["n"], cand["rate"], now, cand["key"]))
                    revived += 1
        except Exception:
            pass

    return {"learned": learned, "retired": retired, "revived": revived,
            "candidates": len(candidates)}


def active(limit: int = MAX_IN_PROMPT) -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT key, text, evidence_n, evidence_rate FROM lessons "
                "WHERE status = ? ORDER BY evidence_rate DESC, evidence_n DESC "
                "LIMIT ?", (ACTIVE, limit)).fetchall()
        return [{"key": k, "text": t, "n": n, "rate": r} for k, t, n, r in rows]
    except Exception:
        return []


def for_prompt() -> str:
    """The block injected into the system prompt. Empty when nothing is known.

    Each line carries its evidence. A rule the agent cannot audit is a rule it
    should not be given — and seeing "8/11" makes the difference between a
    strong pattern and a weak one legible rather than implied.
    """
    rows = active()
    if not rows:
        return ""
    lines = ["[Learned from your own tool history — each with its evidence:]"]
    for r in rows:
        fails = round(r["rate"] * r["n"])
        lines.append(f"  - {r['text']} ({fails}/{r['n']} recent calls)")
    return "\n".join(lines)
