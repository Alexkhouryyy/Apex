"""Outcome tracking — correlate 👍/👎 feedback with skills and reflections.

Three measurement axes:

  skill_outcomes()      — per-skill approval rate for turns where the skill ran
  reflection_outcomes() — pre/post approval rate around each applied reflection
  overall()             — aggregate summary for the telemetry dashboard

The skill join works through (session_id, turn_index) added to skill_usage in
Phase 7.  The reflection join uses timestamps: we find the wall-clock time of
each rated turn via turn_log (role='user'), then compare turns that happened in
a configurable window before vs. after each applied reflection.

All functions are read-only; nothing writes to the DB.
"""
import time
from typing import Optional

from agent import longterm


def skill_rate_in_window(
    name: str,
    start_ts: Optional[float],
    end_ts: Optional[float],
    min_turns: int = 0,
) -> dict:
    """Approval rate for one skill in a half-open timestamp window [start_ts, end_ts).

    Either bound can be None (open-ended). Returns a dict with:
      approval_rate (float|None), rated_turns (int), thumbs_up (int), thumbs_down (int)
    """
    conditions = ["s.name = ?"]
    params: list = [name]
    if start_ts is not None:
        conditions.append("s.ts >= ?")
        params.append(start_ts)
    if end_ts is not None:
        conditions.append("s.ts < ?")
        params.append(end_ts)
    where = " AND ".join(conditions)

    with longterm._conn() as c:
        row = c.execute(
            f"""
            SELECT
                COUNT(f.id)                                                       AS rated,
                COALESCE(SUM(CASE WHEN f.rating =  1 THEN 1 ELSE 0 END), 0)     AS ups,
                COALESCE(SUM(CASE WHEN f.rating = -1 THEN 1 ELSE 0 END), 0)     AS downs
            FROM skill_usage s
            LEFT JOIN turn_feedback f
                   ON f.session_id = s.session_id
                  AND f.turn_index = s.turn_index
            WHERE {where}
            """,
            params,
        ).fetchone()

    rated = row[0] or 0
    ups = row[1] or 0
    downs = row[2] or 0
    return {
        "approval_rate": round(ups / rated, 4) if rated >= max(min_turns, 1) else None,
        "rated_turns": rated,
        "thumbs_up": ups,
        "thumbs_down": downs,
    }


def skill_outcomes(name: Optional[str] = None, days: int = 7) -> list[dict]:
    """Per-skill approval rate for rated turns.

    Only turns where the skill ran AND the user left feedback count toward
    `rated_runs`.  Turns without feedback count toward `total_runs` only.

    Returns one dict per skill, sorted by rated_runs desc.
    """
    cutoff = time.time() - days * 86400
    name_filter = "AND s.name = ?" if name else ""
    params = [cutoff]
    if name:
        params.append(name)

    with longterm._conn() as c:
        rows = c.execute(
            f"""
            SELECT
                s.name,
                COUNT(DISTINCT s.id)                                              AS total_runs,
                COUNT(f.id)                                                       AS rated_runs,
                COALESCE(SUM(CASE WHEN f.rating =  1 THEN 1 ELSE 0 END), 0)     AS ups,
                COALESCE(SUM(CASE WHEN f.rating = -1 THEN 1 ELSE 0 END), 0)     AS downs
            FROM skill_usage s
            LEFT JOIN turn_feedback f
                   ON f.session_id = s.session_id
                  AND f.turn_index = s.turn_index
            WHERE s.ts >= ? {name_filter}
            GROUP BY s.name
            ORDER BY rated_runs DESC, total_runs DESC
            """,
            params,
        ).fetchall()

    out = []
    for row in rows:
        rated = row[2]
        ups = row[3]
        out.append({
            "name": row[0],
            "total_runs": row[1],
            "rated_runs": rated,
            "thumbs_up": ups,
            "thumbs_down": row[4],
            "approval_rate": round(ups / rated, 3) if rated else None,
        })
    return out


def reflection_outcomes(days: int = 30, window_hours: int = 168) -> list[dict]:
    """Pre/post approval rate for every applied reflection in the window.

    For each reflection with status='applied' in the last `days` days:
      - pre_rate: approval rate for rated turns whose wall-clock time falls in
                  [reflection.ts - window_hours, reflection.ts)
      - post_rate: approval rate for rated turns in
                   [reflection.ts, reflection.ts + window_hours)
      - delta: post_rate - pre_rate (positive = improvement)

    Turn timestamps come from turn_log (role='user'), which is written at the
    start of each turn — before the agent responds — so it reliably anchors
    *when* a conversation turn occurred, independent of when feedback arrived.
    """
    cutoff = time.time() - days * 86400
    window_secs = window_hours * 3600

    with longterm._conn() as c:
        reflections = c.execute(
            "SELECT id, ts, kind, content, confidence FROM reflections "
            "WHERE status = 'applied' AND ts >= ? ORDER BY ts DESC",
            (cutoff,),
        ).fetchall()

    out = []
    for refl_id, refl_ts, kind, content, confidence in reflections:
        pre_start = refl_ts - window_secs
        post_end = refl_ts + window_secs

        with longterm._conn() as c:
            pre = c.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN f.rating = 1 THEN 1 ELSE 0 END) AS ups
                FROM turn_feedback f
                JOIN (
                    SELECT session_id, turn_index, MIN(ts) AS turn_ts
                    FROM turn_log WHERE role = 'user'
                    GROUP BY session_id, turn_index
                ) tl ON tl.session_id = f.session_id AND tl.turn_index = f.turn_index
                WHERE tl.turn_ts >= ? AND tl.turn_ts < ?
                """,
                (pre_start, refl_ts),
            ).fetchone()

            post = c.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN f.rating = 1 THEN 1 ELSE 0 END) AS ups
                FROM turn_feedback f
                JOIN (
                    SELECT session_id, turn_index, MIN(ts) AS turn_ts
                    FROM turn_log WHERE role = 'user'
                    GROUP BY session_id, turn_index
                ) tl ON tl.session_id = f.session_id AND tl.turn_index = f.turn_index
                WHERE tl.turn_ts >= ? AND tl.turn_ts < ?
                """,
                (refl_ts, post_end),
            ).fetchone()

        pre_total, pre_ups = (pre[0] or 0), (pre[1] or 0)
        post_total, post_ups = (post[0] or 0), (post[1] or 0)
        pre_rate = (pre_ups / pre_total) if pre_total else None
        post_rate = (post_ups / post_total) if post_total else None
        delta = round(post_rate - pre_rate, 3) if (pre_rate is not None and post_rate is not None) else None

        out.append({
            "reflection_id": refl_id,
            "ts": refl_ts,
            "kind": kind,
            "content": content[:160],
            "confidence": confidence,
            "pre_turns": pre_total,
            "pre_rate": round(pre_rate, 3) if pre_rate is not None else None,
            "post_turns": post_total,
            "post_rate": round(post_rate, 3) if post_rate is not None else None,
            "delta": delta,
        })

    return out


def overall(days: int = 7) -> dict:
    """Dashboard-ready aggregate: approval rate + worst skills + best reflections."""
    from agent.feedback import summary as fb_summary

    fb = fb_summary(days=days)
    skills = skill_outcomes(days=days)
    refls = reflection_outcomes(days=days * 4)  # wider window for reflections

    # Skills with enough data, sorted by approval_rate ascending (worst first)
    rated_skills = [s for s in skills if s["rated_runs"] >= 3]
    worst_skills = sorted(
        rated_skills,
        key=lambda s: s["approval_rate"] if s["approval_rate"] is not None else 1.0,
    )[:5]
    best_skills = sorted(
        rated_skills,
        key=lambda s: s["approval_rate"] if s["approval_rate"] is not None else 0.0,
        reverse=True,
    )[:3]

    # Reflections with measured delta, sorted best first
    delta_refls = [r for r in refls if r["delta"] is not None]
    best_reflections = sorted(delta_refls, key=lambda r: r["delta"], reverse=True)[:3]
    worst_reflections = sorted(delta_refls, key=lambda r: r["delta"])[:3]

    return {
        "days": days,
        "approval_rate": fb["approval_rate"],
        "thumbs_up": fb["thumbs_up"],
        "thumbs_down": fb["thumbs_down"],
        "total_rated_turns": fb["total"],
        "skill_coverage": len([s for s in skills if s["rated_runs"] > 0]),
        "total_skills_run": len(skills),
        "worst_skills": worst_skills,
        "best_skills": best_skills,
        "applied_reflections_in_window": len(refls),
        "reflections_with_delta": len(delta_refls),
        "best_reflections": best_reflections,
        "worst_reflections": worst_reflections,
    }

# ── Real-world outcomes ───────────────────────────────────────────────────────
#
# Everything above measures 👍/👎 — whether you liked an answer at the moment you
# read it. CLAUDE.md §15 asks for something different and much harder:
#
#     Apex recommends: Use CV version B.
#     Later: Version B generated 3 interviews. Version A generated 0.
#
# Liking an answer and the answer working are different variables, and they come
# apart precisely where it matters: confident, fluent, wrong advice is rated
# highly at the time. So this stores results that arrive *later* and links them
# back to the recommendation that caused them.
#
# It cannot be automatic. Nothing on this machine can observe whether you got the
# interview, so the loop closes only when you tell it. That is a real limit, and
# `coverage()` reports it rather than letting a handful of recorded outcomes
# masquerade as a track record.

import json as _json

MIN_OUTCOMES_FOR_RATE = 5


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS recommendation_outcomes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                ts             REAL NOT NULL,
                recommended_at REAL,
                recommendation TEXT NOT NULL,
                action_taken   TEXT DEFAULT '',
                result         TEXT NOT NULL,
                success        INTEGER,
                impact         REAL,
                domain         TEXT DEFAULT '',
                turn_id        INTEGER
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_outcomes_ts "
                  "ON recommendation_outcomes(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_outcomes_domain "
                  "ON recommendation_outcomes(domain, ts DESC)")


def record(recommendation: str, result: str, success: Optional[bool] = None,
           action_taken: str = "", impact: Optional[float] = None,
           domain: str = "", recommended_at: Optional[float] = None,
           turn_id: Optional[int] = None) -> int:
    """Record what actually happened after Apex recommended something.

    `success` is deliberately tri-state. None means "reported, outcome unclear" —
    most real outcomes are partial, and forcing them into a boolean would turn
    the rate into a measure of how decisively things get logged.
    """
    if not (recommendation or "").strip() or not (result or "").strip():
        raise ValueError("an outcome needs both the recommendation and what happened")
    now = time.time()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO recommendation_outcomes "
            "(ts, recommended_at, recommendation, action_taken, result, success, "
            " impact, domain, turn_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (now, recommended_at, recommendation.strip(), action_taken.strip(),
             result.strip(), None if success is None else int(bool(success)),
             impact, (domain or "").strip().lower(), turn_id))
        return int(cur.lastrowid)


def recommendation_accuracy(days: int = 180, domain: Optional[str] = None) -> dict:
    """How often did taking Apex's advice actually work out?

    Counts only outcomes with a decided success value; `undecided` is reported
    separately rather than folded in, because an outcome nobody could call is
    evidence about the outcome, not about the advice.
    """
    cutoff = time.time() - days * 86400
    q = ("SELECT success, impact, domain FROM recommendation_outcomes "
         "WHERE ts >= ?")
    args: list = [cutoff]
    if domain:
        q += " AND domain = ?"
        args.append(domain.strip().lower())

    with longterm._conn() as c:
        rows = c.execute(q, args).fetchall()

    decided = [r for r in rows if r[0] is not None]
    wins = [r for r in decided if r[0] == 1]
    impacts = [r[1] for r in wins if r[1] is not None]

    rate = round(len(wins) / len(decided), 3) if decided else None
    enough = len(decided) >= MIN_OUTCOMES_FOR_RATE

    return {
        "days": days,
        "domain": domain,
        "recorded": len(rows),
        "decided": len(decided),
        "undecided": len(rows) - len(decided),
        "worked": len(wins),
        "rate": rate if enough else None,
        "mean_impact": round(sum(impacts) / len(impacts), 3) if impacts else None,
        "note": (
            f"{len(decided)} decided outcomes — need {MIN_OUTCOMES_FOR_RATE} "
            f"before a rate means anything."
            if not enough else
            f"{len(wins)}/{len(decided)} recommendations worked out."
        ),
    }


def by_domain(days: int = 180) -> list[dict]:
    """Accuracy split by domain, so 'Apex is good at X, poor at Y' is a
    measurement rather than an impression."""
    with longterm._conn() as c:
        domains = [r[0] for r in c.execute(
            "SELECT DISTINCT domain FROM recommendation_outcomes "
            "WHERE domain != '' AND ts >= ?",
            (time.time() - days * 86400,)).fetchall()]
    out = [recommendation_accuracy(days=days, domain=d) for d in sorted(domains)]
    return [o for o in out if o["decided"]]


def coverage(days: int = 180) -> dict:
    """What fraction of rated turns ever got a real-world outcome?

    The honest headline for §15. A high accuracy over four recorded outcomes is
    not a track record, and without this number it would read like one.
    """
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        outcomes = c.execute(
            "SELECT COUNT(*) FROM recommendation_outcomes WHERE ts >= ?",
            (cutoff,)).fetchone()[0]
        try:
            rated = c.execute(
                "SELECT COUNT(*) FROM feedback WHERE ts >= ?", (cutoff,)
            ).fetchone()[0]
        except Exception:
            rated = 0
    # The split matters more than the total. An accuracy figure built entirely
    # from self-report and one built from observation are different claims, and
    # a single blended number hides which one you actually have.
    try:
        from agent import observed as _observed
        by_source = _observed.split(days)
    except Exception:
        by_source = {"observed": 0, "reported": outcomes, "observed_share": None}

    obs = by_source.get("observed", 0)
    if obs and obs == outcomes:
        note = ("Every outcome here was observed by Apex — tool results, test "
                "runs — rather than reported.")
    elif obs:
        note = (f"{obs} of {outcomes} outcomes were observed by Apex; the rest "
                f"were reported. Self-reported outcomes are the weaker half.")
    else:
        note = ("Outcomes are recorded by you, not observed. This ratio is the "
                "ceiling on what the accuracy figure is worth.")

    return {
        "days": days,
        "outcomes_recorded": outcomes,
        "turns_rated": rated,
        "ratio": round(outcomes / rated, 3) if rated else None,
        "observed": obs,
        "reported": by_source.get("reported", 0),
        "observed_share": by_source.get("observed_share"),
        "note": note,
    }
