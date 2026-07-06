"""Autonomous Cortex — OODA loop that advances goals without being explicitly asked.

Ticked every 5 minutes from AwarenessMonitor._review_loop. On each tick it:
  1. Reads active goals + world state (orient)
  2. Asks Haiku which safe action to take next (decide)
  3. Either executes it immediately (allowlisted 'always' tools) or
     stages it as a pending_action and pushes a notification (confirm tools)

Autonomy leash — trusted allowlist:
  'always' → execute without asking (read-only, safe operations)
  'confirm' → push notification to user's phone; stage in pending_actions table
"""
import json
import time
from typing import Optional, Callable

import config
from agent import longterm, goals as goals_mod, telemetry


ALLOWLIST: dict[str, str] = {
    "search_web":    "always",
    "read_file":     "always",
    "search_memory": "always",
    "recall":        "always",
    "query_web":     "always",
    "run_python":    "always",
    "list_goals":    "always",
    "write_file":    "confirm",
    "bash":          "confirm",
    "send_email":    "confirm",
    "sms_send":      "confirm",
    "browser_click": "confirm",
    "browser_press": "confirm",
}

_TICK_INTERVAL = 300.0  # seconds between autonomous ticks
_last_tick: float = 0.0
_notify_fn: Optional[Callable] = None


def set_notify_fn(fn: Callable) -> None:
    global _notify_fn
    _notify_fn = fn


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                goal_id INTEGER,
                tool TEXT NOT NULL,
                inputs_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status, ts DESC)"
        )


def list_pending(status: str = "pending") -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, ts, goal_id, tool, inputs_json, rationale, status "
                "FROM pending_actions WHERE status = ? ORDER BY ts DESC LIMIT 20",
                (status,),
            ).fetchall()
    except Exception:
        return []
    return [
        {
            "id": r[0], "ts": r[1], "goal_id": r[2], "tool": r[3],
            "inputs": json.loads(r[4]), "rationale": r[5], "status": r[6],
        }
        for r in rows
    ]


def approve_action(action_id: int) -> str:
    """Execute an approved pending action and mark it done."""
    with longterm._conn() as c:
        row = c.execute(
            "SELECT goal_id, tool, inputs_json, rationale FROM pending_actions "
            "WHERE id = ? AND status = 'pending'",
            (action_id,),
        ).fetchone()
    if not row:
        return f"Action #{action_id} not found or already processed."
    goal_id, tool, inputs_json, rationale = row
    inputs = json.loads(inputs_json)
    result = _execute_tool(tool, inputs)
    with longterm._conn() as c:
        c.execute(
            "UPDATE pending_actions SET status = 'approved' WHERE id = ?", (action_id,)
        )
    if goal_id:
        try:
            goals_mod.update_goal(goal_id, progress_note=f"[approved] {result[:300]}")
        except Exception:
            pass
    return result


def reject_action(action_id: int) -> str:
    with longterm._conn() as c:
        c.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = ?", (action_id,)
        )
    return f"Action #{action_id} rejected."


def _execute_tool(tool: str, inputs: dict) -> str:
    """Execute an allowlisted safe tool. Returns a short result string."""
    try:
        if tool in ("search_web", "query_web"):
            from tools.search import web_search
            results = web_search(inputs.get("query", ""), max_results=3)
            return str(results)[:500]
        elif tool == "read_file":
            path = inputs.get("path", "")
            with open(path) as f:
                return f.read(2000)
        elif tool in ("search_memory", "recall"):
            results = longterm.recall(inputs.get("query", ""), limit=5)
            return "\n".join(r.get("content", "") for r in results)[:500]
        elif tool == "run_python":
            from tools import sandbox
            code = inputs.get("code", "")
            # DECISION (a): self-initiated code runs in Docker, never on the host.
            try:
                backend = sandbox.autonomous_backend()
            except sandbox.SandboxUnavailable:
                return ("[cortex] run_python needs the Docker sandbox for autonomous "
                        "execution, which is unavailable — skipped (not run on host).")
            res = backend.run_python(code, timeout=10)
            return (res["stdout"] + res["stderr"])[:500]
        else:
            # Confirm-tier tools (write_file, bash, send_email, sms_send, browser_*)
            # have no read-only executor here. Once the user has APPROVED the staged
            # action, run it through the agent's real tool dispatcher — which also
            # applies the safety pattern gate as defense-in-depth. Previously this
            # returned "[cortex] no executor", so approving a staged action did nothing.
            from agent.core import _execute_tool as _real_execute
            return _real_execute(tool, inputs)
    except Exception as e:
        return f"[cortex] {tool} failed: {e}"


_DECIDE_PROMPT = """\
You are the autonomous cortex of an AI agent. Your job is to find ONE small,
safe action to take RIGHT NOW that advances the user's active goals.

ACTIVE GOALS:
{goals}

CURRENT WORLD STATE:
{world_state}

RECENT EVENTS (last 30 min):
{events}

AVAILABLE TOOLS (no confirmation needed):
- search_web(query) — search the web for information
- read_file(path) — read a local file
- search_memory(query) — search long-term agent memory
- run_python(code) — run a small Python snippet

TOOLS THAT NEED USER CONFIRMATION:
- write_file(path, content)
- bash(command)
- send_email(to, subject, body)
- sms_send(to, body)

Rules:
- Pick the single most useful action. Do not invent tasks.
- Prefer 'always' tools over 'confirm' tools.
- If there's genuinely nothing useful to do, output exactly: SKIP
- Do not repeat an action that was already done recently.

Output ONLY valid JSON (no prose, no markdown):
{{"goal_id": <int or null>, "tool": "<name>", "inputs": {{}}, "rationale": "<1 sentence>"}}
"""


def tick(client, world_state: str, events: list[dict], force: bool = False) -> Optional[dict]:
    """Run one OODA cycle. Called from AwarenessMonitor._review_loop every 5 min."""
    global _last_tick
    now = time.time()
    if not force and now - _last_tick < _TICK_INTERVAL:
        return None
    _last_tick = now

    active_goals = goals_mod.list_goals(active_only=True)
    if not active_goals:
        return None

    # Skip if budget is exhausted
    try:
        from agent import budget as budget_mod
        budget_err = budget_mod.check()
        if budget_err:
            print(f"[Cortex] Skipping tick — {budget_err}")
            return None
    except Exception:
        pass

    goals_text = "\n".join(
        f"#{g['id']} [{g['horizon']}] {g['title']}" for g in active_goals[:6]
    )
    events_text = "\n".join(
        f"[{e['source']}] {e['content']}" for e in events[-15:]
    ) or "none"

    prompt = _DECIDE_PROMPT.format(
        goals=goals_text,
        world_state=world_state or "(no world state yet)",
        events=events_text,
    )

    try:
        resp = telemetry.create(
            client,
            call_site="agent.cortex/decide",
            model=config.PROACTIVE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
    except Exception as e:
        print(f"[Cortex] Decide call failed: {e}")
        return None

    if "SKIP" in text[:20]:
        return None

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return None

    try:
        decision = json.loads(text[start:end])
    except Exception:
        return None

    tool = decision.get("tool", "")
    if not tool:
        return None

    level = ALLOWLIST.get(tool, "confirm")
    goal_id = decision.get("goal_id")
    inputs = decision.get("inputs", {})
    rationale = decision.get("rationale", "")

    if level == "always":
        result = _execute_tool(tool, inputs)
        if goal_id:
            try:
                goals_mod.update_goal(goal_id, progress_note=f"[auto] {result[:300]}")
            except Exception:
                pass
        print(f"[Cortex] Auto-executed {tool!r}: {result[:80]}")
        decision["result"] = result
        decision["executed"] = True
    else:
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO pending_actions (ts, goal_id, tool, inputs_json, rationale) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, goal_id, tool, json.dumps(inputs), rationale),
            )
        if _notify_fn:
            try:
                _notify_fn(
                    title="Apex wants to act",
                    body=f"{tool}: {rationale}",
                    kind="cortex_approval",
                    priority="normal",
                    url="/?tab=cortex",
                )
            except Exception:
                pass
        print(f"[Cortex] Staged {tool!r} for approval: {rationale}")
        decision["executed"] = False

    return decision
