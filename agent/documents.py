"""Writing-first documents — a DB-backed editor with AI-assisted edits.

A lightweight document store (title + Markdown body) plus an AI edit primitive:
select text (or the whole doc), give an instruction or pick a preset (improve,
shorten, expand, fix grammar, change tone), and get revised text back to apply.

Storage is a dedicated `documents` table in the longterm SQLite DB — simple,
listable, and independent of the Obsidian vault (a doc can still be exported to
a note on demand from the dashboard).
"""
from __future__ import annotations

import time

import config
from agent import longterm, provider

# Optional broadcaster (injected by the dashboard) so any write — whether from the
# editor or from the agent's document_write tool — live-refreshes open Documents tabs.
_broadcast_fn = None


def set_broadcaster(fn) -> None:
    global _broadcast_fn
    _broadcast_fn = fn


def _broadcast(doc_id: int) -> None:
    if _broadcast_fn:
        try:
            _broadcast_fn({"type": "document_saved", "id": doc_id})
        except Exception:
            pass


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL DEFAULT 'Untitled',
                content    TEXT NOT NULL DEFAULT '',
                format     TEXT NOT NULL DEFAULT 'markdown',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at DESC)")


def _snippet(content: str, n: int = 120) -> str:
    s = " ".join((content or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def list_documents() -> list[dict]:
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, title, content, format, created_at, updated_at "
            "FROM documents ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {"id": r[0], "title": r[1], "snippet": _snippet(r[2]), "format": r[3],
         "words": len((r[2] or "").split()), "created_at": r[4], "updated_at": r[5]}
        for r in rows
    ]


def get(doc_id: int) -> dict | None:
    with longterm._conn() as c:
        r = c.execute(
            "SELECT id, title, content, format, created_at, updated_at FROM documents WHERE id = ?",
            (int(doc_id),),
        ).fetchone()
    if not r:
        return None
    return {"id": r[0], "title": r[1], "content": r[2], "format": r[3],
            "created_at": r[4], "updated_at": r[5]}


def create(title: str = "Untitled", content: str = "", fmt: str = "markdown") -> dict:
    now = time.time()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO documents (title, content, format, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title or "Untitled", content or "", fmt or "markdown", now, now),
        )
        doc_id = cur.lastrowid
    _broadcast(doc_id)
    return get(doc_id)


def update(doc_id: int, title: str | None = None, content: str | None = None) -> dict | None:
    sets, vals = [], []
    if title is not None:
        sets.append("title = ?"); vals.append(title)
    if content is not None:
        sets.append("content = ?"); vals.append(content)
    if not sets:
        return get(doc_id)
    sets.append("updated_at = ?"); vals.append(time.time())
    vals.append(int(doc_id))
    with longterm._conn() as c:
        c.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = ?", vals)
    _broadcast(int(doc_id))
    return get(doc_id)


def delete(doc_id: int) -> bool:
    with longterm._conn() as c:
        cur = c.execute("DELETE FROM documents WHERE id = ?", (int(doc_id),))
        return cur.rowcount > 0


# --- AI editing -------------------------------------------------------------

_EDIT_SYS = (
    "You are a precise writing editor. Apply the user's instruction to the TEXT and "
    "return ONLY the revised text — no preamble, no commentary, no code fences unless "
    "the text itself is code. Preserve the author's voice and meaning unless the "
    "instruction says otherwise. Keep Markdown formatting intact."
)

_PRESETS = {
    "improve":  "Improve the clarity, flow, and word choice without changing the meaning.",
    "shorten":  "Make this significantly more concise while keeping all key points.",
    "expand":   "Expand this with more detail, examples, and depth.",
    "grammar":  "Fix grammar, spelling, and punctuation. Change nothing else.",
    "formal":   "Rewrite in a more formal, professional tone.",
    "casual":   "Rewrite in a warmer, more casual tone.",
    "continue": "Continue writing naturally from where the text ends. Return ONLY the new continuation to append.",
}


def ai_edit(text: str, instruction: str = "", preset: str = "", max_tokens: int = 2000) -> dict:
    """Return {'result': revised_text} or {'error': ...}.

    `preset` (improve/shorten/expand/grammar/formal/casual/continue) maps to a
    built-in instruction; a freeform `instruction` overrides/augments it.
    """
    text = text or ""
    if not text.strip() and preset != "continue":
        return {"error": "No text to edit."}
    instr = _PRESETS.get(preset, "")
    if instruction.strip():
        instr = f"{instr} {instruction.strip()}".strip() if instr else instruction.strip()
    if not instr:
        return {"error": "No instruction or preset given."}

    user = f"INSTRUCTION: {instr}\n\nTEXT:\n{text}"
    model = getattr(config, "AGENT_MODEL", None) or "claude-opus-5"
    try:
        result = provider.complete(model, _EDIT_SYS, user, max_tokens=max_tokens)
    except Exception as e:
        return {"error": f"AI edit failed: {e}"}
    return {"result": result.strip()}


def export_to_vault(doc_id: int, folder: str = "Documents") -> dict:
    """Write a document into the Obsidian vault as a note. Explicit user action,
    so the write-approval gate is bypassed (the click is the approval)."""
    d = get(doc_id)
    if not d:
        return {"error": "Document not found."}
    try:
        from agent import vault
        status = vault.write_note(
            d["title"] or "Untitled", d["content"] or "",
            folder=folder, _bypass_approval=True,
        )
    except Exception as e:
        return {"error": f"Vault export failed: {e}"}
    return {"ok": True, "status": status}
