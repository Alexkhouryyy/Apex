"""Work delegated to a node, and the four ways that quietly goes wrong.

Step 5 of `docs/PHASE_6_7_PLAN.md`. A task is a request for a machine to do
something it has the hardware or the software for — render in Blender, read the
camera, run a local model. The Core decides what should happen; the node decides
whether it is allowed to, which is step 6's business and deliberately not this
file's.

## The queue carries a request, never an approval

There is no `approved` column and no way to add one. A delegated command runs
through the node's own `safety.check`, `mcp_policy.enforce` and
`subagent_scope.check` at execution time, on the node. A queue that could carry
permission with it would undo the entire permission model in one field — and it
would look like a feature.

## The four failures this exists to prevent

**A task for a node that is asleep.** Accepted, never runs, nothing says so. It
is `queued` with the node named, and `describe()` says "waiting for
<node>" — never "done", and never an error either, because waiting is the
correct state and the laptop opening its lid resolves it.

**A node that claims a task and dies.** `claimed` forever, holding work nobody
else will touch. Claims are **leases**: `lease_expires_at` is a wall-clock
deadline, and `sweep()` returns an expired task to the queue with `attempts`
incremented. Nothing has to notice the crash.

**A task that fails every time.** An infinite retry loop wearing a feature's
clothes. `max_attempts` then `dead`, with the last error kept — a dead task that
threw away why it died makes the next person reproduce it.

**A task nothing can ever do.** Queued optimistically, invisible forever.
Eligibility is checked at claim time against `agent/capabilities`, so a node
never claims work it cannot do; and `describe()` reports that nothing currently
can, with the age of the queue, so the difference between "waiting for the
laptop" and "waiting for a machine that does not exist" is visible.

## Why staleness does not block submission

A capability record goes stale after `capabilities.MAX_AGE_SECONDS`, and an
offline laptop's records are stale *by definition* — it has not been there to
probe. Refusing on staleness would refuse every task the moment the laptop
sleeps, which is the exact case this queue exists for.

So only a **fresh, definite `no`** refuses at submission: "Blender is not
installed there" does not become true by waiting, and telling you now beats a
task that sits queued forever. A stale `no`, an `unknown`, or a stale `yes` all
queue, because the honest answer is that we will find out when the machine comes
back.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Optional

from agent import capabilities, longterm

QUEUED, CLAIMED, DONE, FAILED, DEAD = "queued", "claimed", "done", "failed", "dead"

DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3


class TaskRefused(ValueError):
    """Submission refused for a reason waiting will not change."""


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS node_tasks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   REAL NOT NULL,
                kind         TEXT NOT NULL,
                payload      TEXT NOT NULL DEFAULT '{}',
                node         TEXT NOT NULL DEFAULT '',
                capability   TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'queued',
                claimed_by   TEXT NOT NULL DEFAULT '',
                claimed_at   REAL NOT NULL DEFAULT 0,
                lease_expires_at REAL NOT NULL DEFAULT 0,
                attempts     INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                result       TEXT NOT NULL DEFAULT '',
                error        TEXT NOT NULL DEFAULT '',
                finished_at  REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_node_tasks_claimable "
                  "ON node_tasks(status, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_node_tasks_lease "
                  "ON node_tasks(status, lease_expires_at)")


# ── submitting ───────────────────────────────────────────────────────────────

def submit(kind: str, *, capability: str = "", node: str = "",
           payload: Optional[dict] = None,
           max_attempts: int = DEFAULT_MAX_ATTEMPTS,
           now: Optional[float] = None) -> dict:
    """Queue work. Refuses only for a reason that waiting will not fix."""
    kind = str(kind or "").strip()
    if not kind:
        raise TaskRefused("a task needs a kind")
    now = time.time() if now is None else now

    if capability and node:
        rec = capabilities.of(node, now=now).get(capability)
        if rec and rec["state"] == capabilities.NO and not rec["stale"]:
            raise TaskRefused(
                f"{node} cannot do '{capability}' — checked "
                f"{int(now - rec['verified_at'])}s ago: {rec['detail']}. "
                f"Queuing it would leave a task waiting for something that is "
                f"not going to happen.")

    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO node_tasks (created_at, kind, payload, node, capability,"
            " max_attempts) VALUES (?, ?, ?, ?, ?, ?)",
            (now, kind, json.dumps(payload or {}), node, capability,
             max(1, int(max_attempts))))
        c.commit()
    return {"id": cur.lastrowid, "status": QUEUED, "kind": kind,
            "node": node, "capability": capability}


# ── leases ───────────────────────────────────────────────────────────────────

def sweep(now: Optional[float] = None) -> dict:
    """Return expired leases to the queue; retire the ones out of attempts.

    Called at the top of `claim()` and `describe()` rather than from a timer.
    A sweep that only runs on a schedule is a sweep that has not run when you
    look, and this is cheap: two indexed UPDATEs over a table that holds work in
    flight, not history.
    """
    now = time.time() if now is None else now
    with longterm._conn() as c:
        dead = c.execute(
            "UPDATE node_tasks SET status = ?, finished_at = ?,"
            " error = CASE WHEN error = '' THEN"
            "   'the node claimed it and never finished; out of attempts'"
            "   ELSE error END"
            " WHERE status = ? AND lease_expires_at < ? AND attempts >= max_attempts",
            (DEAD, now, CLAIMED, now)).rowcount
        requeued = c.execute(
            "UPDATE node_tasks SET status = ?, claimed_by = '', claimed_at = 0,"
            " lease_expires_at = 0"
            " WHERE status = ? AND lease_expires_at < ?",
            (QUEUED, CLAIMED, now)).rowcount
        c.commit()
    return {"requeued": requeued, "dead": dead}


def claim(node_id: str, *, lease_seconds: int = DEFAULT_LEASE_SECONDS,
          now: Optional[float] = None,
          exclude: Optional[set] = None) -> Optional[dict]:
    """Take the oldest task this node is actually able to run, or None.

    Eligibility is checked against `agent/capabilities` here rather than at
    submission, because a node's abilities are a fact about the node at the
    moment it asks — not about the moment someone typed the request.

    `exclude` skips task ids the caller has already handled this pass. Without
    it a task that fails and returns to the queue is claimed again immediately
    by the same loop, and each claim SPENDS AN ATTEMPT — so a transient failure
    would exhaust its retry budget in microseconds, on retries that had no time
    to become different.
    """
    exclude = exclude or set()
    if not node_id:
        raise ValueError("claim() needs a node id")
    now = time.time() if now is None else now
    sweep(now)

    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, kind, payload, capability, attempts, max_attempts"
            " FROM node_tasks WHERE status = ? AND (node = '' OR node = ?)"
            " ORDER BY id", (QUEUED, node_id)).fetchall()
        for task_id, kind, payload, cap, attempts, max_attempts in rows:
            if task_id in exclude:
                continue
            if cap and not capabilities.can(node_id, cap, now=now):
                continue
            # The UPDATE is the lock. Checking then writing would let two nodes
            # — or two threads of one node — both believe they won.
            won = c.execute(
                "UPDATE node_tasks SET status = ?, claimed_by = ?, claimed_at = ?,"
                " lease_expires_at = ?, attempts = attempts + 1"
                " WHERE id = ? AND status = ?",
                (CLAIMED, node_id, now, now + max(1, int(lease_seconds)),
                 task_id, QUEUED)).rowcount
            if not won:
                continue
            c.commit()
            return {"id": task_id, "kind": kind,
                    "payload": json.loads(payload or "{}"),
                    "capability": cap, "attempt": attempts + 1,
                    "max_attempts": max_attempts,
                    "lease_expires_at": now + lease_seconds}
    return None


def _finish(task_id: int, node_id: str, status: str, *, result: str = "",
            error: str = "", now: Optional[float] = None) -> bool:
    """True if this node still held the lease. False means it had already
    expired and someone else may be running the task — the node should stop
    rather than write a result for work that is no longer its own."""
    now = time.time() if now is None else now
    with longterm._conn() as c:
        changed = c.execute(
            "UPDATE node_tasks SET status = ?, result = ?, error = ?,"
            " finished_at = ?, lease_expires_at = 0"
            " WHERE id = ? AND status = ? AND claimed_by = ?",
            (status, str(result)[:4000], str(error)[:2000], now,
             int(task_id), CLAIMED, node_id)).rowcount
        c.commit()
    return bool(changed)


def complete(task_id: int, node_id: str, result: str = "",
             now: Optional[float] = None) -> bool:
    return _finish(task_id, node_id, DONE, result=result, now=now)


def fail(task_id: int, node_id: str, error: str, now: Optional[float] = None) -> bool:
    """Record a failure. Out of attempts it is `dead`; otherwise back to the
    queue for another node — or the same one later."""
    now = time.time() if now is None else now
    with longterm._conn() as c:
        row = c.execute("SELECT attempts, max_attempts, status, claimed_by"
                        " FROM node_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not row or row[2] != CLAIMED or row[3] != node_id:
            return False
        attempts, max_attempts = row[0], row[1]
        if attempts >= max_attempts:
            c.execute("UPDATE node_tasks SET status = ?, error = ?, finished_at = ?,"
                      " lease_expires_at = 0 WHERE id = ?",
                      (DEAD, str(error)[:2000], now, int(task_id)))
        else:
            c.execute("UPDATE node_tasks SET status = ?, claimed_by = '',"
                      " claimed_at = 0, lease_expires_at = 0, error = ?"
                      " WHERE id = ?", (QUEUED, str(error)[:2000], int(task_id)))
        c.commit()
    return True


def abandon(task_id: int, node_id: str, error: str,
            now: Optional[float] = None) -> bool:
    """Retire a task immediately, without spending its remaining attempts.

    For failures that cannot come out differently: a tool that is not on this
    node's allowlist, a kind nothing knows how to run, a malformed payload, an
    action safety refused. Retrying those changes nothing, and worse, it spends
    max_attempts in seconds and the task ends up recorded as "out of attempts"
    when the truth was "not allowed".
    """
    now = time.time() if now is None else now
    with longterm._conn() as c:
        changed = c.execute(
            "UPDATE node_tasks SET status = ?, error = ?, finished_at = ?,"
            " lease_expires_at = 0 WHERE id = ? AND status = ? AND claimed_by = ?",
            (DEAD, str(error)[:2000], now, int(task_id), CLAIMED, node_id)).rowcount
        c.commit()
    return bool(changed)


# ── looking at it ────────────────────────────────────────────────────────────

def get(task_id: int) -> Optional[dict]:
    with longterm._conn() as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM node_tasks WHERE id = ?",
                        (int(task_id),)).fetchone()
    return dict(row) if row else None


def describe(task_id: int, now: Optional[float] = None) -> str:
    """One sentence a person can act on.

    The point of this function is that "queued for a sleeping laptop" and
    "queued for a machine that can never do it" are the same row and different
    problems, and a status word alone cannot tell them apart.
    """
    now = time.time() if now is None else now
    sweep(now)
    t = get(task_id)
    if not t:
        return f"There is no task {task_id}."
    age = int(now - t["created_at"])
    if t["status"] == DONE:
        return f"Task {task_id} ({t['kind']}) finished on {t['claimed_by']}."
    if t["status"] == DEAD:
        return (f"Task {task_id} ({t['kind']}) gave up after {t['attempts']} "
                f"attempt(s): {t['error'] or 'no reason recorded'}")
    if t["status"] == CLAIMED:
        left = int(t["lease_expires_at"] - now)
        return (f"Task {task_id} ({t['kind']}) is running on {t['claimed_by']}, "
                f"attempt {t['attempts']}; its lease expires in {left}s and it "
                f"returns to the queue if that passes.")
    # Queued — the case that needs the most care.
    who = t["node"] or "any node"
    if t["capability"]:
        able = [n["device_id"] for n in capabilities.summary(now=now)
                if n["capabilities"].get(t["capability"], {}).get("usable")]
        if t["node"] and t["node"] not in able:
            return (f"Task {task_id} ({t['kind']}) is waiting for {t['node']} to "
                    f"come back and confirm it can do '{t['capability']}'. "
                    f"Queued {age}s ago. Nothing has been done to it.")
        if not t["node"] and not able:
            return (f"Task {task_id} ({t['kind']}) is waiting for any node that "
                    f"can do '{t['capability']}', and no known node currently "
                    f"can. Queued {age}s ago.")
    return (f"Task {task_id} ({t['kind']}) is waiting for {who}. "
            f"Queued {age}s ago. Nothing has been done to it.")


def pending(now: Optional[float] = None) -> list[dict]:
    now = time.time() if now is None else now
    sweep(now)
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, kind, node, capability, status, attempts, created_at"
            " FROM node_tasks WHERE status IN (?, ?) ORDER BY id",
            (QUEUED, CLAIMED)).fetchall()
    cols = ("id", "kind", "node", "capability", "status", "attempts", "created_at")
    return [dict(zip(cols, r)) for r in rows]
