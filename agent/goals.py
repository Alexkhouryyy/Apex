"""Goals + self-evaluation: strategic horizons for the agent.

Tracks long-term goals (day/week/month/quarter) with progress notes and
runs periodic self-evaluation that reviews what got done, what stalled,
and what to focus on next.
"""
import json
import time
from typing import Optional

from agent import longterm
from agent import telemetry

VALID_HORIZONS = {"day", "week", "month", "quarter"}
VALID_STATUSES = {"active", "paused", "done", "abandoned"}


def init_db():
    """Ensure goals + goal_progress tables exist in the shared memory DB."""
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                horizon TEXT NOT NULL,
                deadline REAL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS goal_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                note TEXT NOT NULL,
                score INTEGER,
                FOREIGN KEY(goal_id) REFERENCES goals(id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status, horizon)")

    # The completion-contract gate lives in update_goal, so its tables must exist
    # wherever goals do — otherwise an older DB would block every goal close.
    try:
        from agent import verification
        verification.init_db()
    except Exception:
        pass


def set_goal(title: str, description: str = "", horizon: str = "week", deadline_iso: Optional[str] = None) -> str:
    horizon = horizon.lower().strip()
    if horizon not in VALID_HORIZONS:
        return f"Invalid horizon {horizon!r}. Use one of: {sorted(VALID_HORIZONS)}"

    deadline_ts: Optional[float] = None
    if deadline_iso:
        try:
            from datetime import datetime
            deadline_ts = datetime.fromisoformat(deadline_iso).timestamp()
        except Exception:
            return f"Invalid deadline format. Use ISO 8601 (e.g. 2026-06-30 or 2026-06-30T18:00:00)."

    now = time.time()
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO goals (title, description, horizon, deadline, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (title, description, horizon, deadline_ts, now, now),
        )
        new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return f"Goal #{new_id} set: {title!r} (horizon={horizon}, deadline={deadline_iso or 'none'})"


def list_goals(active_only: bool = True, horizon: Optional[str] = None) -> list[dict]:
    sql = "SELECT id, title, description, horizon, deadline, status, created_at, updated_at FROM goals"
    where, params = [], []
    if active_only:
        where.append("status = 'active'")
    if horizon:
        where.append("horizon = ?")
        params.append(horizon.lower())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with longterm._conn() as c:
        rows = c.execute(sql, params).fetchall()

    goals = []
    for r in rows:
        goal = {
            "id": r[0], "title": r[1], "description": r[2], "horizon": r[3],
            "deadline": r[4], "status": r[5], "created_at": r[6], "updated_at": r[7],
        }
        # Attach recent progress
        with longterm._conn() as c:
            prog = c.execute(
                "SELECT ts, note, score FROM goal_progress WHERE goal_id = ? ORDER BY ts DESC LIMIT 5",
                (r[0],)
            ).fetchall()
        goal["recent_progress"] = [{"ts": p[0], "note": p[1], "score": p[2]} for p in prog]
        goals.append(goal)
    return goals


def update_goal(goal_id: int, status: Optional[str] = None, progress_note: Optional[str] = None,
                score: Optional[int] = None, evidence: str = "", _force: bool = False) -> str:
    now = time.time()
    actions = []

    if status:
        status = status.lower().strip()
        if status not in VALID_STATUSES:
            return f"Invalid status {status!r}. Use one of: {sorted(VALID_STATUSES)}"

        # Work verification gate: a goal carrying a completion contract cannot be
        # closed on the agent's say-so — the contract must actually pass. Goals
        # with no contract stay closable (backward compatible) but are recorded
        # as unverified so the two cases never blur.
        verdict_note = ""
        if status == "done" and not _force:
            try:
                from agent import verification
                res = verification.verify(goal_id, evidence=evidence)
                if res["contracted"] and not res["passed"]:
                    return (f"Goal #{goal_id} NOT closed — completion contract failed:\n"
                            + verification.format_result(res)
                            + "\nFix the work (or pass _force=True to override, which is recorded).")
                verdict_note = "verified" if res["contracted"] else "unverified (no contract)"
            except Exception as e:
                # Fail CLOSED: if the gate itself is broken we do not silently pass.
                return f"Goal #{goal_id} NOT closed — verification could not run: {e}"
        elif status == "done" and _force:
            verdict_note = "FORCE-CLOSED without verification"

        with longterm._conn() as c:
            c.execute("UPDATE goals SET status = ?, updated_at = ? WHERE id = ?", (status, now, goal_id))
        actions.append(f"status -> {status}" + (f" [{verdict_note}]" if verdict_note else ""))

    if progress_note:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO goal_progress (goal_id, ts, note, score) VALUES (?, ?, ?, ?)",
                (goal_id, now, progress_note, score),
            )
            c.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (now, goal_id))
        actions.append(f"progress note added" + (f" (score={score})" if score else ""))

    if not actions:
        return "Nothing to update — pass status or progress_note."
    return f"Goal #{goal_id}: " + ", ".join(actions)


def evaluate_recent_work(client, days: int = 7) -> str:
    """Self-evaluation: pull recent activity, ask Claude to assess and recommend."""
    cutoff = time.time() - (days * 86400)

    with longterm._conn() as c:
        sessions = c.execute(
            "SELECT started_at, ended_at, summary FROM sessions WHERE started_at >= ? ORDER BY started_at",
            (cutoff,),
        ).fetchall()
        goals = c.execute(
            "SELECT id, title, description, horizon, status, updated_at FROM goals "
            "WHERE status IN ('active','done','paused') AND updated_at >= ?",
            (cutoff,),
        ).fetchall()
        progress = c.execute(
            "SELECT g.title, gp.ts, gp.note, gp.score FROM goal_progress gp "
            "JOIN goals g ON g.id = gp.goal_id WHERE gp.ts >= ? ORDER BY gp.ts",
            (cutoff,),
        ).fetchall()
        completed_tasks = c.execute(
            "SELECT description, last_run, run_count FROM scheduled_tasks WHERE last_run >= ?",
            (cutoff,),
        ).fetchall()

    # Build a digest for Claude
    digest_parts = [f"# Activity review — last {days} days\n"]
    if goals:
        digest_parts.append("## Goals")
        for g in goals:
            digest_parts.append(f"- #{g[0]} [{g[3]}/{g[4]}] {g[1]}: {g[2][:120]}")
    if progress:
        digest_parts.append("\n## Progress notes")
        for p in progress:
            digest_parts.append(f"- ({p[0]}) {p[2]}" + (f" [score={p[3]}]" if p[3] else ""))
    if sessions:
        digest_parts.append(f"\n## Sessions: {len(sessions)} this period")
        for s in sessions[-5:]:
            if s[2]:
                digest_parts.append(f"  - {s[2][:200]}")
    if completed_tasks:
        digest_parts.append(f"\n## Scheduled tasks fired: {len(completed_tasks)}")
        for t in completed_tasks[-5:]:
            digest_parts.append(f"  - {t[0][:100]} (x{t[2]})")

    digest = "\n".join(digest_parts)

    import config
    resp = telemetry.create(
        client,
        call_site="agent.goals/evaluate_recent_work",
        model=config.AGENT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": (
            "You are evaluating your own recent work as the user's AI agent.\n\n"
            f"{digest}\n\n"
            "Give an honest self-assessment in 4 sections:\n"
            "1. **What got done** (1-3 sentences)\n"
            "2. **What stalled or wasn't done well** (be candid)\n"
            "3. **What I learned about the user this period**\n"
            "4. **What to focus on this coming week** (concrete, prioritised)\n\n"
            "Be direct. No fluff."
        )}],
    )
    text = resp.content[0].text.strip()

    # Persist the eval as a session summary entry
    summary_id = longterm.start_session()
    longterm.end_session(summary_id, f"[Weekly self-eval] {text[:400]}")

    return text


def active_goals_for_prompt() -> str:
    """Return goals + world state + user preferences to inject at session start."""
    goals = list_goals(active_only=True)
    lines = []

    if goals:
        lines.append("[Active goals — keep these in mind:]")
        for g in goals[:8]:
            deadline = ""
            if g["deadline"]:
                from datetime import datetime
                deadline = f" (by {datetime.fromtimestamp(g['deadline']).date()})"
            lines.append(f"  - [{g['horizon']}] {g['title']}{deadline}")

    try:
        from agent import world_model as _wm
        world_state = _wm.get()
        if world_state:
            lines.append(f"\n[Current world state:]\n  {world_state}")
    except Exception:
        pass

    try:
        from agent import prefs as _prefs
        pref_digest = _prefs.get()
        if pref_digest:
            lines.append(f"\n[Your communication preferences:]\n{pref_digest[:400]}")
    except Exception:
        pass

    # Self-knowledge: only present when a tool is genuinely misbehaving, so a
    # healthy day costs no tokens. Lets the agent AVOID a broken tool rather than
    # only discovering the problem after the call fails.
    try:
        from agent import trajectory as _traj
        reliability = _traj.reliability_digest()
        if reliability:
            lines.append(f"\n[Tool reliability warning:]\n  {reliability}")
    except Exception:
        pass

    return "\n".join(lines)
