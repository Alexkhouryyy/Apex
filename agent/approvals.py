"""Write-approval gate — stage memory/note/skill writes for the user to approve.

When config.MEMORY_WRITE_APPROVAL (or SKILL_WRITE_APPROVAL) is True, mutating
calls are parked in the staged_writes table and a push notification is sent
instead of being applied immediately. The user approves or rejects from the
dashboard (or via the approve/reject helpers), at which point the write is
applied with the approval gate bypassed.

Mirrors the cortex pending_actions pattern so the dashboard UX is consistent.
"""
import json
import time
from typing import Callable, Optional

from agent import longterm

_notify_fn: Optional[Callable] = None


def set_notify_fn(fn: Callable) -> None:
    global _notify_fn
    _notify_fn = fn


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS staged_writes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,          -- memory | note | skill
                summary TEXT NOT NULL,       -- human-readable one-liner
                payload_json TEXT NOT NULL,  -- args needed to apply the write
                status TEXT NOT NULL DEFAULT 'pending'
            )
        """)


def _summarize(kind: str, payload: dict) -> str:
    if kind == "memory":
        return f"{payload.get('action')} -> {payload.get('target')}: {str(payload.get('content'))[:80]}"
    if kind == "note":
        return f"note '{payload.get('title')}' in {payload.get('folder', 'Notes')}"
    if kind == "skill":
        return f"skill '{payload.get('name')}': {str(payload.get('description'))[:80]}"
    if kind == "email":
        return f"email to {payload.get('to')}: {str(payload.get('subject'))[:80]}"
    return kind


def stage(kind: str, payload: dict) -> str:
    """Park a write for approval. Returns a message telling the agent it's queued."""
    summary = _summarize(kind, payload)
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO staged_writes (ts, kind, summary, payload_json, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (time.time(), kind, summary, json.dumps(payload)),
        )
        write_id = cur.lastrowid
    if _notify_fn:
        try:
            _notify_fn(
                title="Apex wants to save something",
                body=f"[{kind}] {summary}",
                kind="write_approval",
            )
        except Exception:
            pass
    return (
        f"[STAGED for approval #{write_id}] {kind} write held pending review "
        f"(approval gate is on). The user will approve or reject it from the dashboard."
    )


def list_pending(status: str = "pending") -> list[dict]:
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT id, ts, kind, summary, payload_json, status "
                "FROM staged_writes WHERE status = ? ORDER BY ts DESC LIMIT 50",
                (status,),
            ).fetchall()
    except Exception:
        return []
    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "summary": r[3],
         "payload": json.loads(r[4]), "status": r[5]}
        for r in rows
    ]


def _apply(kind: str, payload: dict) -> str:
    """Apply a previously staged write, bypassing the approval gate."""
    if kind == "memory":
        return longterm.save_memory_entry(
            target=payload["target"], action=payload["action"],
            content=payload.get("content"), old_text=payload.get("old_text"),
            _bypass_approval=True,
        )
    if kind == "note":
        from agent import vault
        return vault.write_note(
            payload["title"], payload.get("content", ""),
            folder=payload.get("folder", "Notes"),
            tags=payload.get("tags"), links=payload.get("links"),
            _bypass_approval=True,
        )
    if kind == "skill":
        from agent import skill_md
        return skill_md.manage(
            "create", name=payload.get("name"),
            description=payload.get("description"), content=payload.get("content"),
            _bypass_approval=True,
        )
    if kind == "email":
        from tools import email_box
        return email_box.send(
            payload["to"], payload.get("subject", ""), payload.get("body", ""),
            in_reply_to=payload.get("in_reply_to"),
        )
    return f"Unknown staged kind: {kind!r}"


def approve(write_id) -> str:
    """Approve one staged write, or 'all' to approve every pending write."""
    if str(write_id).lower() == "all":
        results = []
        for w in list_pending("pending"):
            results.append(approve(w["id"]))
        return f"Approved {len(results)} write(s)." if results else "Nothing pending."

    # Atomically CLAIM the row before applying, so two concurrent approvals
    # (e.g. approve(id) racing approve("all")) can't both fire the side effect.
    with longterm._conn() as c:
        cur = c.execute(
            "UPDATE staged_writes SET status = 'approving' WHERE id = ? AND status = 'pending'",
            (int(write_id),),
        )
        if cur.rowcount != 1:
            return f"Staged write #{write_id} not found or already processed."
        row = c.execute(
            "SELECT kind, payload_json FROM staged_writes WHERE id = ?",
            (int(write_id),),
        ).fetchone()
    kind, payload_json = row
    result = _apply(kind, json.loads(payload_json))
    with longterm._conn() as c:
        c.execute("UPDATE staged_writes SET status = 'approved' WHERE id = ?", (int(write_id),))
    return f"Approved #{write_id}: {result}"


def reject(write_id) -> str:
    """Reject one staged write, or 'all' to reject every pending write."""
    if str(write_id).lower() == "all":
        with longterm._conn() as c:
            c.execute("UPDATE staged_writes SET status = 'rejected' WHERE status = 'pending'")
        return "Rejected all pending writes."
    with longterm._conn() as c:
        c.execute("UPDATE staged_writes SET status = 'rejected' WHERE id = ?", (int(write_id),))
    return f"Staged write #{write_id} rejected."
