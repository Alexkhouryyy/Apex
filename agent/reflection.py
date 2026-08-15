"""Nightly closed-loop learning.

`consolidate(client, hours=24)` reviews the last N hours of activity:
  - session summaries
  - awareness events
  - sub-agent outputs
  - completed scheduled tasks
  - goal progress
  - existing memories (for staleness checks)

Then asks Claude to extract durable patterns and writes them to the `reflections`
table. High-confidence reflections (≥0.85) auto-apply: new memories saved,
stale ones forgotten, implicit goal progress recorded, entities inserted into
the knowledge graph. Lower-confidence go to the dashboard for approval.
"""
import json
import threading
import time
from typing import Callable, Optional

import config
from agent import longterm, goals, entities, telemetry


_awareness_drain: Optional[Callable[[], list[dict]]] = None


def set_awareness_drain(fn: Callable[[], list[dict]]) -> None:
    """Wire the awareness monitor's drain so reflection can pull events."""
    global _awareness_drain
    _awareness_drain = fn


def _gather(hours: int) -> dict:
    cutoff = time.time() - hours * 3600
    with longterm._conn() as c:
        sessions = c.execute(
            "SELECT id, started_at, ended_at, summary FROM sessions WHERE started_at >= ?",
            (cutoff,),
        ).fetchall()
        memories = c.execute(
            "SELECT id, ts, kind, content, importance FROM memories ORDER BY importance DESC, ts DESC LIMIT 50"
        ).fetchall()
        goal_rows = c.execute(
            "SELECT id, title, status, horizon FROM goals WHERE status IN ('active','done','paused')"
        ).fetchall()
        progress = c.execute(
            "SELECT g.title, gp.ts, gp.note FROM goal_progress gp JOIN goals g ON g.id = gp.goal_id "
            "WHERE gp.ts >= ? ORDER BY gp.ts",
            (cutoff,),
        ).fetchall()
        tasks = c.execute(
            "SELECT description, last_run, run_count FROM scheduled_tasks WHERE last_run >= ?",
            (cutoff,),
        ).fetchall()
        ent_rows = c.execute(
            "SELECT name, kind FROM entities ORDER BY importance DESC, last_seen DESC LIMIT 30"
        ).fetchall()

    awareness_events = []
    if _awareness_drain:
        try:
            awareness_events = _awareness_drain()
        except Exception:
            pass

    # Pull recent perception log (durable store) — richer than the drained ring buffer
    perception_events = []
    try:
        from agent import perception as _perc
        perception_events = _perc.recent(since_hours=hours, limit=200)
    except Exception:
        pass

    return {
        "sessions": [{"id": s[0], "started_at": s[1], "ended_at": s[2], "summary": s[3]} for s in sessions],
        "memories": [{"id": m[0], "kind": m[2], "content": m[3], "importance": m[4]} for m in memories],
        "goals": [{"id": g[0], "title": g[1], "status": g[2], "horizon": g[3]} for g in goal_rows],
        "progress": [{"goal_title": p[0], "ts": p[1], "note": p[2]} for p in progress],
        "tasks": [{"description": t[0], "last_run": t[1], "run_count": t[2]} for t in tasks],
        "entities": [{"name": e[0], "kind": e[1]} for e in ent_rows],
        "awareness_events": awareness_events[-200:],  # cap (ring buffer drain)
        "perception_events": perception_events,       # durable persisted log
    }


def _build_digest(data: dict) -> str:
    lines = []

    if data["sessions"]:
        lines.append(f"## Sessions ({len(data['sessions'])})")
        for s in data["sessions"][-10:]:
            if s["summary"]:
                lines.append(f"  - {s['summary'][:250]}")

    if data["memories"]:
        lines.append("\n## Existing top memories (assess for staleness)")
        for m in data["memories"][:30]:
            lines.append(f"  - #{m['id']} [{m['kind']} imp={m['importance']}] {m['content'][:160]}")

    if data["goals"]:
        lines.append("\n## Goals")
        for g in data["goals"]:
            lines.append(f"  - #{g['id']} [{g['horizon']}/{g['status']}] {g['title']}")

    if data["progress"]:
        lines.append("\n## Goal progress in window")
        for p in data["progress"]:
            lines.append(f"  - ({p['goal_title']}) {p['note'][:160]}")

    if data["tasks"]:
        lines.append("\n## Scheduled tasks fired")
        for t in data["tasks"]:
            lines.append(f"  - {t['description'][:120]} (x{t['run_count']})")

    if data["entities"]:
        lines.append("\n## Known entities")
        for e in data["entities"]:
            lines.append(f"  - {e['name']} ({e['kind']})")

    if data["awareness_events"]:
        lines.append(f"\n## Awareness events ({len(data['awareness_events'])} drained)")
        for e in data["awareness_events"][-50:]:
            lines.append(f"  - [{e.get('source','?')}] {str(e.get('content',''))[:160]}")

    if data.get("perception_events"):
        lines.append(f"\n## Perception log ({len(data['perception_events'])} events from durable store)")
        for e in data["perception_events"][-60:]:
            lines.append(f"  - [{e.get('source','?')}] {str(e.get('content',''))[:160]}")

    return "\n".join(lines) if lines else "(no recent activity)"


_PROMPT = """You are reviewing your own recent work as the user's AI agent.
Your job: extract durable patterns from the past period and propose changes to your own memory + behavior.

Below is a digest of activity. For each insight, output a JSON object with these fields:
  - kind: one of "pattern" | "insight" | "correction" | "stale_memory_flag" | "entity_extract" | "goal_progress"
  - content: short human-readable description
  - confidence: 0.0-1.0 (how sure you are this is durable / actionable)
  - action: a structured action to apply if accepted. Schema by kind:
      pattern/insight: {"type": "remember", "content": str, "kind": "fact|preference|project|decision|note", "importance": 1-10}
      correction: {"type": "remember", "content": str, "kind": "...", "importance": ..., "supersedes_id": int}
      stale_memory_flag: {"type": "forget", "memory_id": int}
      entity_extract: {"type": "entity", "name": str, "kind": "person|project|place|concept|tool|file|event|org", "properties": {...}}
                       OR {"type": "relate", "from": str, "from_kind": "...", "to": str, "to_kind": "...", "kind": "verb-phrase"}
      goal_progress: {"type": "goal_progress", "goal_id": int, "note": str, "score": 1-10}

Output ONLY a JSON array. No prose. Example:
[
  {"kind": "pattern", "content": "User prefers concise responses under 100 words", "confidence": 0.9,
   "action": {"type": "remember", "content": "Prefers responses under 100 words.", "kind": "preference", "importance": 8}},
  {"kind": "entity_extract", "content": "Sam is Alex's co-founder", "confidence": 0.95,
   "action": {"type": "relate", "from": "Alex", "from_kind": "person", "to": "Sam", "to_kind": "person", "kind": "co-founder"}}
]

Be conservative — only flag patterns supported by the evidence. If nothing meaningful, return [].

DIGEST:
"""


_LAST_RUN_KEY = "last_consolidation"
_running = threading.Lock()

# Fallback clock. If the timestamp cannot be persisted, `is_due` would be True on
# every tick and consolidation — a full model call — would fire every 15 seconds
# forever. Degrading to a per-process cadence is a small loss; an unbounded spend
# loop triggered by a missing table is not.
_last_run_memory = 0.0


def last_run_ts() -> float:
    """When consolidation last started, persisted across restarts.

    In-memory alone would be wrong for the same reason the scheduler's
    missed-fire bug was wrong: a machine that restarts more often than the
    interval would never reach it, and the feature would look enabled while
    never running. So persistence is the primary source and memory is the floor.
    """
    stored = 0.0
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT updated_at FROM world_state WHERE key = ?", (_LAST_RUN_KEY,)
            ).fetchone()
        stored = row[0] if row else 0.0
    except Exception:
        stored = 0.0
    return max(stored, _last_run_memory)


def _mark_run() -> None:
    global _last_run_memory
    now = time.time()
    _last_run_memory = now              # set first: this is the guard that holds
    try:                                # even when the write below fails
        with longterm._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS world_state ("
                "key TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
            )
            c.execute(
                "INSERT OR REPLACE INTO world_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_LAST_RUN_KEY, "1", now),
            )
    except Exception as e:
        print(f"[Reflection] run time not persisted ({e}); "
              f"falling back to this process's clock")


def interval_seconds() -> float:
    return max(1.0, float(getattr(config, "REFLECTION_INTERVAL_HOURS", 6))) * 3600.0


def is_due(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    last = last_run_ts()
    if last <= 0:
        return True                      # never run — the state Apex shipped in
    return (now - last) >= interval_seconds()


def consolidate_if_due(client, hours: int = 24) -> dict | None:
    """Consolidate on a cadence. Returns None when not due or already running.

    Until this existed, `consolidate` had exactly one caller: the `reflect_now`
    tool. Nothing scheduled it, so Apex's memory consolidation ran only when the
    model happened to choose to — which is to say, almost never. The machinery
    was built, tested and effectively dormant.

    The timestamp is written *before* the work, so a consolidation that crashes
    waits out the interval instead of retrying in a hot loop.
    """
    if client is None or not is_due():
        return None
    if not _running.acquire(blocking=False):
        return None                      # a slow pass is still going; skip this tick
    try:
        _mark_run()
        result = consolidate(client, hours=hours)
        created = result.get("created", 0) if isinstance(result, dict) else 0
        print(f"[Reflection] consolidation ran — {created} reflection(s) created")
        # Procedural memory rides the same cadence: episodic consolidation asks
        # what happened, this asks what worked. Isolated so a failure here
        # cannot cost the reflection pass that already succeeded.
        try:
            from agent import lessons as _lessons
            stats = _lessons.run(client)
            if stats.get("learned") or stats.get("retired"):
                print(f"[Lessons] +{stats['learned']} learned, "
                      f"-{stats['retired']} retired, "
                      f"{stats.get('revived', 0)} revived")
            if isinstance(result, dict):
                result["lessons"] = stats
        except Exception as e:
            print(f"[Lessons] pass failed: {e}")
        # Initiative rides the same cadence: what the evidence now implies is
        # worth doing. Stages proposals only — never creates a goal. Isolated
        # for the same reason as above.
        try:
            from agent import initiative as _init
            taken = _init.run()
            if isinstance(result, dict):
                result["initiative"] = taken
        except Exception as e:
            print(f"[Initiative] pass failed: {e}")
        return result
    except Exception as e:
        print(f"[Reflection] consolidation failed: {e}")
        return {"error": str(e), "created": 0, "applied": 0}
    finally:
        _running.release()


def consolidate(client, hours: int = 24, autosave: bool = True) -> dict:
    """Run a consolidation pass. Returns counts of created/auto-applied reflections."""
    data = _gather(hours)
    digest = _build_digest(data)

    try:
        resp = telemetry.create(
            client,
            call_site="agent.reflection/consolidate",
            model=config.AGENT_MODEL,
            max_tokens=4000,
            thinking={"type": "enabled", "budget_tokens": config.THINKING_BUDGET},
            messages=[{"role": "user", "content": _PROMPT + digest}],
        )
    except Exception as e:
        return {"error": f"Claude call failed: {e}", "created": 0, "applied": 0}

    text = ""
    for b in resp.content:
        if getattr(b, "type", "") == "text":
            text += b.text
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1 or end <= start:
        return {"error": "no JSON array in response", "raw": text[:400], "created": 0, "applied": 0}
    try:
        arr = json.loads(text[start:end])
    except Exception as e:
        return {"error": f"JSON parse failed: {e}", "raw": text[start:end][:400], "created": 0, "applied": 0}

    session_ids = ",".join(str(s["id"]) for s in data["sessions"])
    created = 0
    now = time.time()
    pending_actions: list[dict] = []

    # Insert all reflection rows in one transaction, collect actions to apply after.
    # Applying actions inside the same write transaction causes "database is locked"
    # because _apply_action() opens its own connection.
    with longterm._conn() as c:
        for item in arr:
            kind = (item.get("kind") or "insight").strip()
            content = (item.get("content") or "").strip()
            confidence = float(item.get("confidence") or 0.5)
            action = item.get("action") or {}
            should_apply = autosave and confidence >= config.REFLECTION_AUTO_APPLY_THRESHOLD
            status = "applied" if should_apply else "pending"
            c.execute(
                """INSERT INTO reflections (ts, kind, content, source_session_ids, confidence, status, action_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now, kind, content, session_ids, confidence, status, json.dumps(action)),
            )
            created += 1
            if should_apply:
                pending_actions.append(action)

    # Apply actions after the write transaction has been committed and closed.
    applied = 0
    for action in pending_actions:
        try:
            _apply_action(action)
            applied += 1
        except Exception as e:
            print(f"[Reflection] auto-apply failed: {e}")

    # Self-improving skills: rewrite any skill that has been failing repeatedly.
    skills_refined = 0
    try:
        skills_refined = refine_skills(client, hours=hours).get("refined", 0)
    except Exception as e:
        print(f"[Reflection] skill refinement failed: {e}")

    # Auto-rollback: check whether any recent skill rewrite hurt approval rate.
    rollback_summary = {}
    try:
        from agent import rollback as rollback_mod
        rollback_summary = rollback_mod.check_rewrites()
    except Exception as e:
        print(f"[Reflection] rollback check failed: {e}")

    # Preference distillation — run weekly, skipped if not enough time has passed
    prefs_updated = False
    try:
        from agent import prefs as prefs_mod
        result = prefs_mod.distill(client)
        prefs_updated = bool(result)
    except Exception as e:
        print(f"[Reflection] prefs distillation failed: {e}")

    return {
        "created": created, "applied": applied, "pending": created - applied,
        "skills_refined": skills_refined,
        "rollback": rollback_summary,
        "prefs_updated": prefs_updated,
    }


_SKILL_REFINE_PROMPT = """One of your installed skills has been failing repeatedly. \
Rewrite its `run` function to fix the errors below.

SKILL: {name}

CURRENT SOURCE:
{source}

RECENT ERRORS ({count} failures):
{errors}

Output ONLY a JSON object:
  {{"code": "def run(inputs: dict) -> str:\\n    ..."}}
The code must define `def run(inputs: dict) -> str`, keep the same input contract, \
and use only the Python standard library. If you cannot determine a safe fix, \
output {{"code": ""}}."""


def refine_skills(client, hours: int = 24) -> dict:
    """Rewrite skills that failed repeatedly in the window. Returns counts."""
    from agent import skills as skills_mod

    candidates = skills_mod.failure_stats(hours=hours, min_failures=3)
    refined = 0
    staged = 0
    for cand in candidates:
        name = cand["name"]
        source = skills_mod.read_source(name)
        if not source:
            continue
        prompt = _SKILL_REFINE_PROMPT.format(
            name=name,
            source=source[:6000],
            count=cand["failures"],
            errors="\n".join(f"  - {e}" for e in cand["errors"]) or "  (no error text captured)",
        )
        try:
            resp = telemetry.create(
                client,
                call_site="agent.reflection/refine_skill",
                model=config.AGENT_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            print(f"[Reflection] skill refine call failed for {name!r}: {e}")
            continue
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        s, e = text.find("{"), text.rfind("}") + 1
        if s < 0 or e <= s:
            continue
        try:
            new_code = json.loads(text[s:e]).get("code", "")
        except Exception:
            continue
        if not new_code.strip():
            continue
        result = skills_mod.create_skill(name, skills_mod.get_description(name), new_code, _trigger="reflection")
        print(f"[Reflection] refined skill {name!r}: {result}")
        if "STAGED" in result:
            # Model-written executable code now waits for approval rather than
            # installing itself. Counted separately: nothing has been refined
            # until a human says yes, and reporting otherwise would overstate
            # what actually changed.
            staged += 1
            try:
                from agent import notify as _notify
                _notify.notify(
                    "Apex proposes a skill rewrite",
                    f"Rewrote \u201c{name}\u201d after {cand['failures']} recent "
                    f"failures \u2014 approve it to install.",
                    kind="write_approval", url="/?tab=approvals",
                    dedup_key=f"evo-stage-{name}",
                )
            except Exception as _e:
                print(f"[Reflection] stage notify failed: {_e}")
            continue
        if "created and loaded" in result:
            refined += 1
            # Surface the self-improvement to the user across all devices.
            try:
                from agent import notify as _notify
                _notify.notify(
                    "Apex refined a skill",
                    f"Rewrote “{name}” after {cand['failures']} recent failures. "
                    f"It will auto-roll-back if approval drops.",
                    kind="evolution", url="/?tab=evolution",
                    dedup_key=f"evo-refine-{name}",
                )
            except Exception as _e:
                print(f"[Reflection] refine notify failed: {_e}")
    return {"candidates": len(candidates), "refined": refined,
            "staged": staged}


def _apply_action(action: dict) -> None:
    t = (action.get("type") or "").lower()
    if t == "remember":
        longterm.remember(
            action["content"],
            kind=action.get("kind", "note"),
            importance=int(action.get("importance", 5)),
        )
        supers = action.get("supersedes_id")
        if supers:
            try:
                longterm.forget(int(supers))
            except Exception:
                pass
    elif t == "forget":
        longterm.forget(int(action["memory_id"]))
    elif t == "entity":
        entities.upsert_entity(
            action["name"], kind=action.get("kind", "concept"),
            properties=action.get("properties") or {},
        )
    elif t == "relate":
        entities.relate(
            action["from"], action["to"], action.get("kind", "related_to"),
            from_kind=action.get("from_kind", "concept"),
            to_kind=action.get("to_kind", "concept"),
        )
    elif t == "goal_progress":
        goals.update_goal(
            int(action["goal_id"]),
            progress_note=action.get("note", ""),
            score=action.get("score"),
        )


def list_reflections(status: str = "pending", limit: int = 50) -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, ts, kind, content, source_session_ids, confidence, status, action_json "
            "FROM reflections WHERE status = ? ORDER BY ts DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    out = []
    for r in rows:
        try:
            action = json.loads(r[7] or "{}")
        except Exception:
            action = {}
        out.append({
            "id": r[0], "ts": r[1], "kind": r[2], "content": r[3],
            "source_session_ids": r[4], "confidence": r[5], "status": r[6],
            "action": action,
        })
    return out


def apply_reflection(reflection_id: int, accept: bool = True) -> str:
    with longterm._conn() as c:
        row = c.execute(
            "SELECT status, action_json FROM reflections WHERE id = ?",
            (reflection_id,),
        ).fetchone()
    if row is None:
        return f"No reflection #{reflection_id}."
    if row[0] != "pending":
        return f"Reflection #{reflection_id} already {row[0]}."

    if not accept:
        with longterm._conn() as c:
            c.execute("UPDATE reflections SET status = 'rejected' WHERE id = ?", (reflection_id,))
        return f"Rejected reflection #{reflection_id}."

    try:
        action = json.loads(row[1] or "{}")
    except Exception:
        action = {}
    try:
        _apply_action(action)
    except Exception as e:
        return f"Apply failed: {e}"
    with longterm._conn() as c:
        c.execute("UPDATE reflections SET status = 'applied' WHERE id = ?", (reflection_id,))
    return f"Applied reflection #{reflection_id}."


# ---------------------------------------------------------------------------
# Jarvis: me.md — human-readable user profile digest
# ---------------------------------------------------------------------------

_MEME_PATH = __import__("pathlib").Path.home() / "Documents" / "Apex" / "me.md"
_MEME_MARKER = "<!-- apex:auto-generated -->"


def generate_profile_digest(client) -> str:
    """Write ~/Documents/Apex/me.md from high-importance memories.

    Skips the write if the file has been user-modified (no auto-generation marker).
    Returns a status string.
    """
    try:
        if not getattr(config, "PROFILE_DIGEST_ENABLED", True):
            return "Profile digest disabled."

        # Skip if the user has edited the file (removed our marker)
        if _MEME_PATH.exists():
            existing = _MEME_PATH.read_text(encoding="utf-8", errors="replace")
            if _MEME_MARKER not in existing:
                return "me.md exists and appears user-edited — skipping auto-update."

        with longterm._conn() as c:
            rows = c.execute(
                "SELECT kind, content, importance FROM memories WHERE importance >= 7 "
                "ORDER BY importance DESC, ts DESC LIMIT 60"
            ).fetchall()
            ent_rows = c.execute(
                "SELECT name, kind, properties FROM entities ORDER BY importance DESC, last_seen DESC LIMIT 20"
            ).fetchall()

        if not rows:
            return "No high-importance memories yet — me.md not generated."

        mem_lines = "\n".join(f"- [{r[0]}] {r[1]}" for r in rows)
        ent_lines = "\n".join(f"- {r[0]} ({r[1]})" for r in ent_rows)

        prompt = (
            "You are writing a clean, readable personal profile document for the user's AI assistant. "
            "Based on the following memories and known entities, write a concise Markdown profile. "
            "Sections: ## About Me, ## Ongoing Projects, ## Preferences & Style, ## Key People. "
            "Be specific and factual. Use bullet points. Keep it under 400 words.\n\n"
            f"High-importance memories:\n{mem_lines}\n\n"
            f"Known entities:\n{ent_lines}"
        )
        resp = telemetry.create(
            client,
            call_site="agent.reflection/profile_digest",
            model=config.PROACTIVE_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        body = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()

        _MEME_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MEME_PATH.write_text(
            f"{_MEME_MARKER}\n# Me — User Profile\n_Auto-generated by Apex. "
            f"Remove this comment line to prevent future auto-updates._\n\n{body}\n",
            encoding="utf-8",
        )
        return f"me.md updated ({len(body)} chars) at {_MEME_PATH}."
    except Exception as e:
        return f"Profile digest failed: {e}"


def start_profile_digest_loop(client) -> None:
    """Background thread: regenerate me.md every PROFILE_DIGEST_INTERVAL_SECONDS."""
    import threading, time as _t

    interval = getattr(config, "PROFILE_DIGEST_INTERVAL_SECONDS", 3600)

    def _loop():
        _t.sleep(60)  # give Apex time to boot before first run
        while True:
            try:
                result = generate_profile_digest(client)
                print(f"[Reflection] {result}")
            except Exception as e:
                print(f"[Reflection] profile digest loop error: {e}")
            _t.sleep(interval)

    threading.Thread(target=_loop, daemon=True, name="ProfileDigest").start()
