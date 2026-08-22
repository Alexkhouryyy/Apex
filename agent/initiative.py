"""Initiative — surfacing work the evidence already implies.

`cortex.tick()` returns None when there are no active goals, and its prompt says
"Do not invent tasks." Apex advances goals you set and originates nothing; with
an empty goal list it does nothing at all. That is a correct default, and this
module lifts it only in a narrow, bounded way.

**This is not "Apex decides what it wants."** That framing oversells what is
safe or useful to build. Every proposal here is derived from data Apex already
collected, in exactly two shapes:

  1. a recurring failure that step 2 has already turned into a measured lesson
  2. a goal *you* set that has gone quiet past its own horizon

Both concern your stated intent or a measured defect. Neither asks a model what
would be good to want. There is deliberately no open-ended "propose something
useful" path — that version is simultaneously the dangerous one and the one that
generates slop.

Three properties do the safety work, and none of them is a prompt instruction:

- **A proposal cannot become a goal.** It is staged through `approvals`, and the
  only code path from proposal to real goal is `approvals._apply`, which runs
  when you approve. Structural, not advisory.
- **Silence is the default.** No evidence means no proposals, and a healthy Apex
  proposes nothing forever.
- **Declining sticks.** Rejected subjects are remembered. Otherwise "no" means
  "ask again in six hours", which teaches you to stop reading — and a
  rubber-stamped gate is worse than no gate, because it looks like oversight.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import config
from agent import longterm

# Ceiling on unreviewed proposals. Approval fatigue is the failure mode that
# makes the whole review gate worthless, so this is a safety limit rather than
# a politeness one.
MAX_PENDING = 3

# A goal untouched for longer than this, relative to its horizon, has stalled.
# Keys mirror goals.VALID_HORIZONS exactly; an entry for a horizon that cannot
# exist is dead code that reads like coverage.
_HORIZON_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 90}
_STALL_MULTIPLIER = 1.0

# How long a declined subject stays declined. Long enough that "no" means no;
# not permanent, because a problem that returns a season later is new evidence.
DECLINE_MEMORY_DAYS = 90

KIND = "goal_proposal"
# Below this, the comparison is anecdote. Deliberately low — this is one
# person's goals, not a population; waiting for 30 would mean never.
MIN_CASES_FOR_EFFECT = 6


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS initiative_log (
                evidence_key TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                state        TEXT NOT NULL,
                ts           REAL NOT NULL
            )
        """)


# --- evidence -----------------------------------------------------------------

def _stall_seconds(horizon: str) -> float:
    return _HORIZON_DAYS.get((horizon or "week").lower(), 7) * 86400 * _STALL_MULTIPLIER


def gather(now: Optional[float] = None) -> list[dict]:
    """Work the evidence implies. Pure reads; no model, no writes.

    Returns [{evidence_key, title, description, horizon, evidence}]. Empty when
    nothing warrants attention, which is the normal case.
    """
    now = time.time() if now is None else now
    out: list[dict] = []

    # 1. Recurring failures, already measured and phrased by agent/lessons.
    try:
        from agent import lessons as _lessons
        for row in _lessons.active():
            fails = round(row["rate"] * row["n"])
            out.append({
                "evidence_key": f"lesson:{row['key']}",
                "title": f"Fix recurring failure: {row['key'].split('|')[0][5:]}",
                "description": (
                    f"{row['text']}\n\n"
                    f"Observed {fails} failures in {row['n']} recent calls."
                ),
                "horizon": "week",
                "evidence": f"{fails}/{row['n']} recent calls failed",
            })
    except Exception as e:
        print(f"[Initiative] lesson evidence unavailable: {e}")

    # 2. Goals you set that have gone quiet past their own horizon.
    try:
        from agent import goals as _goals
        for g in _goals.list_goals(active_only=True):
            idle = now - (g.get("updated_at") or g.get("created_at") or now)
            limit = _stall_seconds(g.get("horizon", "week"))
            overdue = bool(g.get("deadline")) and g["deadline"] < now
            if idle < limit and not overdue:
                continue
            days = int(idle // 86400)
            out.append({
                "evidence_key": f"stalled:{g['id']}",
                "title": f"Stalled goal: {g['title']}",
                "description": (
                    f"Goal #{g['id']} ({g['horizon']}) has had no progress for "
                    f"{days} days"
                    + (" and is past its deadline" if overdue else "")
                    + ". Revive it with a next step, or close it."
                ),
                "horizon": g.get("horizon", "week"),
                "evidence": f"no progress in {days} days"
                            + (", deadline passed" if overdue else ""),
            })
    except Exception as e:
        print(f"[Initiative] goal evidence unavailable: {e}")

    return out


# --- decline memory -----------------------------------------------------------

def _seen(evidence_key: str, now: float) -> bool:
    """True when this subject was already proposed or explicitly declined."""
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT state, ts FROM initiative_log WHERE evidence_key = ?",
                (evidence_key,)).fetchone()
    except Exception:
        return True                     # cannot check -> stay quiet, never spam
    if not row:
        return False
    state, ts = row
    if state == "declined":
        return (now - ts) < DECLINE_MEMORY_DAYS * 86400
    return True                         # already proposed and still on the books


def _remember(evidence_key: str, title: str, state: str) -> None:
    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO initiative_log "
                "(evidence_key, title, state, ts) VALUES (?,?,?,?)",
                (evidence_key, title, state, time.time()))
    except Exception as e:
        print(f"[Initiative] could not record proposal state: {e}")


def decline(evidence_key: str) -> None:
    """Record that a subject was rejected, so it is not raised again."""
    _remember(evidence_key, evidence_key, "declined")


# --- proposing ----------------------------------------------------------------

def pending_count() -> int:
    try:
        from agent import approvals
        return len([p for p in approvals.list_pending() if p.get("kind") == KIND])
    except Exception:
        return MAX_PENDING              # unknown -> assume full, propose nothing


def propose(now: Optional[float] = None) -> list[dict]:
    """Stage proposals for anything new the evidence implies.

    Returns what was staged, which is [] on a healthy day. Nothing here creates
    a goal: `approvals.stage` parks it, and only your approval runs `_apply`.
    """
    init_db()
    now = time.time() if now is None else now

    room = MAX_PENDING - pending_count()
    if room <= 0:
        return []                       # unreviewed proposals already waiting

    staged: list[dict] = []
    try:
        from agent import approvals
        for item in gather(now=now):
            if len(staged) >= room:
                break
            if _seen(item["evidence_key"], now):
                continue
            approvals.stage(KIND, {
                "evidence_key": item["evidence_key"],
                "title": item["title"],
                "description": item["description"],
                "horizon": item["horizon"],
                "evidence": item["evidence"],
            })
            _remember(item["evidence_key"], item["title"], "proposed")
            staged.append(item)
    except Exception as e:
        print(f"[Initiative] propose failed: {e}")
    return staged


def run(now: Optional[float] = None) -> dict:
    """One initiative pass, for the consolidation heartbeat."""
    try:
        staged = propose(now=now)
        if staged:
            print(f"[Initiative] proposed {len(staged)} goal(s) for review")
        return {"proposed": len(staged),
                "keys": [s["evidence_key"] for s in staged]}
    except Exception as e:
        print(f"[Initiative] pass failed: {e}")
        return {"proposed": 0, "keys": []}

# ── Did any of this help? ──────────────────────────────────────────────────────

def intervention_effect(days: int = 90, now: Optional[float] = None) -> dict:
    """Measure whether proposing something for a stalled goal unstuck it.

    CLAUDE.md §16 asks Apex to identify blockers and adjust strategy. It does
    propose — but nothing measured whether proposing changed anything, so the
    row sat UNPROVEN: not untested, unmeasured.

    For every goal_proposal raised about a stalled goal, this asks one question:
    did that goal record progress afterwards? Grouped by whether you accepted
    the proposal, which gives a comparison group for free.

    ## The caveat is the point, so it ships inside the result

    This is not a trial. You choose which proposals to accept, and you accept
    the ones about goals you already meant to return to. So accepted proposals
    will precede progress even if the proposing did nothing at all — the
    selection explains the correlation on its own.

    What the number can honestly do is fail: if accepted proposals are followed
    by *no more* progress than declined ones, the feature is not working, and
    that conclusion is safe because selection bias points the other way. A
    positive result is suggestive; a null result is informative.
    """
    now = time.time() if now is None else now
    cutoff = now - days * 86400

    with longterm._conn() as c:
        rows = c.execute(
            "SELECT ts, payload_json, status FROM staged_writes "
            "WHERE kind = ? AND ts >= ? ORDER BY ts",
            (KIND, cutoff)).fetchall()

    groups: dict[str, dict] = {
        "accepted": {"n": 0, "moved": 0},
        "not_accepted": {"n": 0, "moved": 0},
    }
    cases: list[dict] = []

    for ts, payload_json, status in rows:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        key = str(payload.get("evidence_key") or "")
        if not key.startswith("stalled:"):
            continue          # lesson-driven proposals have no goal to move
        try:
            goal_id = int(key.split(":", 1)[1])
        except (IndexError, ValueError):
            continue

        with longterm._conn() as c:
            moved = c.execute(
                "SELECT COUNT(*) FROM goal_progress WHERE goal_id = ? AND ts > ?",
                (goal_id, ts)).fetchone()[0]
            closed = c.execute(
                "SELECT status FROM goals WHERE id = ?", (goal_id,)).fetchone()

        # Closing a stalled goal is a resolution too — deciding to drop it is
        # exactly what the proposal offers as the alternative to reviving it.
        resolved = bool(moved) or bool(closed and closed[0] in ("done", "abandoned"))

        bucket = "accepted" if status == "approved" else "not_accepted"
        groups[bucket]["n"] += 1
        groups[bucket]["moved"] += 1 if resolved else 0
        cases.append({"goal_id": goal_id, "status": status,
                      "progress_after": moved, "resolved": resolved})

    def _rate(g):
        return round(g["moved"] / g["n"], 3) if g["n"] else None

    acc, non = groups["accepted"], groups["not_accepted"]
    acc_rate, non_rate = _rate(acc), _rate(non)
    lift = (round(acc_rate - non_rate, 3)
            if acc_rate is not None and non_rate is not None else None)

    if acc["n"] + non["n"] < MIN_CASES_FOR_EFFECT:
        verdict = (f"Too few interventions to say anything "
                   f"({acc['n'] + non['n']}/{MIN_CASES_FOR_EFFECT}).")
    elif lift is None:
        verdict = "Only one group has cases — nothing to compare against."
    elif lift <= 0:
        verdict = ("Accepted proposals are followed by no more progress than "
                   "declined ones. Selection bias favours the opposite result, "
                   "so this is real: the proposing is not helping.")
    else:
        verdict = (f"Accepted proposals precede progress {lift:+.0%} more often. "
                   f"Suggestive, not causal — you accept proposals for goals you "
                   f"already intended to resume.")

    return {
        "days": days,
        "accepted": {**acc, "rate": acc_rate},
        "not_accepted": {**non, "rate": non_rate},
        "lift": lift,
        "cases": cases,
        "verdict": verdict,
        "caveat": ("Observational. Acceptance is your choice, not a random "
                   "assignment, so a positive lift is consistent with the "
                   "proposal doing nothing."),
    }
