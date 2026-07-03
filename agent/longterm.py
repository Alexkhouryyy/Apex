"""Long-term persistent memory backed by SQLite + semantic embeddings.

Tools for the agent:
  - remember(content, kind, importance): store a durable memory with embedding
  - recall(query, limit, semantic): retrieve relevant past memories
  - forget(id): delete a memory

Recall strategy:
  - semantic=True (default when query is a full sentence): cosine similarity over embeddings
  - semantic=False: fast LIKE search for exact keyword matching
  - Falls back to LIKE if embeddings model not loaded
"""
import os
import sqlite3
import time
import json
from contextlib import contextmanager
from typing import Optional

import numpy as np

DB_PATH = os.path.expanduser(os.getenv("DB_PATH", "~/.voice_agent_memory.db"))

_embed_model = None
_embed_lock = None


def _get_embed_model():
    global _embed_model, _embed_lock
    import threading
    if _embed_lock is None:
        _embed_lock = threading.Lock()
    with _embed_lock:
        if _embed_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                print("[Memory] Loading embedding model (all-MiniLM-L6-v2)...")
                _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
                print("[Memory] Embedding model ready.")
            except Exception as e:
                print(f"[Memory] Embedding model unavailable: {e}")
                _embed_model = False  # sentinel: tried and failed
        return _embed_model if _embed_model is not False else None


def _embed(text: str) -> Optional[bytes]:
    model = _get_embed_model()
    if model is None:
        return None
    try:
        vec = model.encode([text], normalize_embeddings=True)[0]
        return vec.astype(np.float32).tobytes()
    except Exception:
        return None


def _cosine_scores(query_vec: np.ndarray, blob_list: list[bytes]) -> list[float]:
    scores = []
    for blob in blob_list:
        if blob is None:
            scores.append(0.0)
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        scores.append(float(np.dot(query_vec, vec)))
    return scores


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                importance INTEGER NOT NULL,
                tags TEXT DEFAULT '',
                embedding BLOB
            )
        """)
        # Add embedding column if migrating from old schema
        try:
            c.execute("ALTER TABLE memories ADD COLUMN embedding BLOB")
        except Exception:
            pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                ended_at REAL,
                summary TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_params TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                last_run REAL,
                run_count INTEGER DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance DESC, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind)")

        # --- Tier-4: reflection engine ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,             -- pattern|insight|correction|stale_memory_flag|entity_extract
                content TEXT NOT NULL,
                source_session_ids TEXT DEFAULT '',
                confidence REAL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'pending',  -- pending|applied|rejected
                action_json TEXT DEFAULT ''     -- structured action to apply if accepted
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_refl_status ON reflections(status, ts DESC)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS memory_audit (
                memory_id INTEGER PRIMARY KEY,
                last_verified_at REAL,
                confidence REAL DEFAULT 1.0,
                supersedes_id INTEGER
            )
        """)

        # --- Tier-4: knowledge graph ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,             -- person|project|place|concept|tool|file|event|org
                properties_json TEXT DEFAULT '{}',
                embedding BLOB,
                created_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                importance INTEGER DEFAULT 5
            )
        """)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ent_name_kind ON entities(name, kind)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ent_kind ON entities(kind, importance DESC)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                properties_json TEXT DEFAULT '{}',
                ts REAL NOT NULL,
                confidence REAL DEFAULT 1.0,
                FOREIGN KEY(from_id) REFERENCES entities(id),
                FOREIGN KEY(to_id) REFERENCES entities(id)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rel_from ON relations(from_id, kind)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rel_to ON relations(to_id, kind)")

        # --- Tier-4: usage telemetry ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                session_id INTEGER,
                turn_index INTEGER,
                call_site TEXT NOT NULL,        -- agent.core/stream | agent.memory | agent.goals | ...
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                thinking_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                cost_usd REAL DEFAULT 0.0,
                tool_calls_json TEXT DEFAULT '[]',
                stop_reason TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_log(session_id, turn_index)")

        # --- Tier-4: per-turn log for episode replay ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS turn_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                session_id INTEGER,
                turn_index INTEGER,
                role TEXT NOT NULL,             -- user|assistant|tool_result
                content_json TEXT NOT NULL,
                tool_calls_json TEXT DEFAULT '[]'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_turn_session ON turn_log(session_id, turn_index)")

        # --- FTS5 index over turn_log for keyword search ---
        had_trigger = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='turn_log_ai'"
        ).fetchone()
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS turn_log_fts "
            "USING fts5(content_json, content='turn_log', content_rowid='id')"
        )
        c.execute("""
            CREATE TRIGGER IF NOT EXISTS turn_log_ai AFTER INSERT ON turn_log BEGIN
              INSERT INTO turn_log_fts(rowid, content_json) VALUES (new.id, new.content_json);
            END
        """)
        if not had_trigger:
            c.execute("INSERT INTO turn_log_fts(turn_log_fts) VALUES('rebuild')")

        # --- Self-improving skills: per-run usage telemetry ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS skill_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                name TEXT NOT NULL,
                success INTEGER NOT NULL,        -- 1 = ok, 0 = raised
                duration REAL,                   -- seconds
                error TEXT,                      -- NULL on success
                session_id INTEGER,              -- ambient telemetry session at call time
                turn_index INTEGER               -- ambient telemetry turn at call time
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_skill_usage_name ON skill_usage(name, ts DESC)")
        # Migration: add session_id / turn_index if upgrading from pre-Phase-7 schema
        for col in ("session_id", "turn_index"):
            try:
                c.execute(f"ALTER TABLE skill_usage ADD COLUMN {col} INTEGER")
            except Exception:
                pass

        # --- Phase 7: Skill rewrite audit + auto-rollback ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS skill_rewrites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,               -- when the rewrite happened
                name TEXT NOT NULL,             -- skill name
                old_source TEXT,                -- full file content before rewrite (NULL if brand new)
                new_source TEXT NOT NULL,       -- full file content after rewrite
                trigger TEXT DEFAULT 'manual',  -- 'manual' | 'reflection'
                pre_approval_rate REAL,         -- approval rate in window before rewrite
                pre_rated_turns INTEGER DEFAULT 0,
                post_approval_rate REAL,        -- filled in by check_rewrites()
                post_rated_turns INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',   -- 'active' | 'confirmed' | 'rolled_back'
                rollback_ts REAL,
                rollback_reason TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rewrites_name ON skill_rewrites(name, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rewrites_status ON skill_rewrites(status, ts DESC)")


@contextmanager
def _conn():
    # busy_timeout: wait up to 5s for a lock instead of instantly raising
    # "database is locked" (background curator/cortex/scheduler threads contend
    # with the main loop). WAL: concurrent readers don't block the writer.
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        yield conn
        conn.commit()
    finally:
        conn.close()


def remember(content: str, kind: str = "fact", importance: int = 5, tags: str = "") -> str:
    kind = kind.lower().strip()
    if kind not in {"fact", "preference", "project", "decision", "note"}:
        kind = "note"
    importance = max(1, min(10, int(importance)))
    embedding = _embed(content)
    with _conn() as c:
        c.execute(
            "INSERT INTO memories (ts, kind, content, importance, tags, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), kind, content, importance, tags, embedding),
        )
        new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return f"Remembered [#{new_id} {kind} importance={importance}]: {content}"


def recall(query: str = "", limit: int = 10, kind: str = "", semantic: bool = True) -> list[dict]:
    """Retrieve memories. Uses semantic search when a query is given and embeddings available."""
    with _conn() as c:
        if kind:
            rows = c.execute(
                "SELECT id, ts, kind, content, importance, tags, embedding FROM memories WHERE kind = ? ORDER BY importance DESC, ts DESC",
                (kind.lower(),)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, ts, kind, content, importance, tags, embedding FROM memories ORDER BY importance DESC, ts DESC"
            ).fetchall()

    if not rows:
        return []

    if query and semantic:
        model = _get_embed_model()
        if model is not None:
            query_vec = model.encode([query], normalize_embeddings=True)[0].astype(np.float32)
            blobs = [r[6] for r in rows]
            scores = _cosine_scores(query_vec, blobs)
            # Blend semantic score with importance
            combined = [(scores[i] + rows[i][4] / 20.0, i) for i in range(len(rows))]
            combined.sort(reverse=True)
            top = [rows[i] for _, i in combined[:int(limit)]]
            return _format_rows(top)
        # Fallback: LIKE
        query_lower = f"%{query}%"
        return _format_rows([r for r in rows if query_lower[1:-1] in r[3].lower() or query_lower[1:-1] in r[5].lower()][:int(limit)])

    elif query:
        q = query.lower()
        return _format_rows([r for r in rows if q in r[3].lower() or q in r[5].lower()][:int(limit)])

    return _format_rows(rows[:int(limit)])


def _format_rows(rows) -> list[dict]:
    return [
        {"id": r[0], "ts": r[1], "kind": r[2], "content": r[3], "importance": r[4], "tags": r[5]}
        for r in rows
    ]


def forget(memory_id: int) -> str:
    with _conn() as c:
        c.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return f"Forgot memory #{memory_id}"


def top_memories(limit: int = 15) -> list[dict]:
    return recall(limit=limit)


def format_for_context(memories: list[dict]) -> str:
    if not memories:
        return ""
    lines = ["[Long-term memory — things I know about you and ongoing context:]"]
    for m in memories:
        lines.append(f"  - ({m['kind']}, importance {m['importance']}) {m['content']}")
    return "\n".join(lines)


def start_session() -> int:
    with _conn() as c:
        c.execute("INSERT INTO sessions (started_at) VALUES (?)", (time.time(),))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def end_session(session_id: int, summary: str = "") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (time.time(), summary, session_id),
        )


def search_turns(query: str, limit: int = 20, session_id: int | None = None) -> list[dict]:
    """Full-text search over turn_log using FTS5. Returns ranked matching turns."""
    import json as _json
    try:
        with _conn() as c:
            if session_id is not None:
                rows = c.execute(
                    """SELECT t.id, t.ts, t.session_id, t.turn_index, t.role, t.content_json
                       FROM turn_log_fts f
                       JOIN turn_log t ON t.id = f.rowid
                       WHERE f.content_json MATCH ? AND t.session_id = ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, session_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT t.id, t.ts, t.session_id, t.turn_index, t.role, t.content_json
                       FROM turn_log_fts f
                       JOIN turn_log t ON t.id = f.rowid
                       WHERE f.content_json MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (query, limit),
                ).fetchall()
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for r in rows:
        try:
            content = _json.loads(r[5])
        except Exception:
            content = r[5]
        results.append({
            "id": r[0], "ts": r[1], "session_id": r[2],
            "turn_index": r[3], "role": r[4], "content": content,
        })
    return results


# ---------------------------------------------------------------------------
# Bounded file-based memory (APEX_MEMORY.md + APEX_USER.md)
# Two plain Markdown files read ONCE at session start (frozen snapshot for
# Anthropic prefix cache). Agent self-manages via the `memory` tool.
# ---------------------------------------------------------------------------
import re as _re
from pathlib import Path as _Path

_APEX_MEMORY_DIR = _Path(getattr(__import__("config"), "VAULT_PATH", "~/.apex/memory")).expanduser() / "Memory"
_MEMORY_FILE = _APEX_MEMORY_DIR / "APEX_MEMORY.md"
_USER_FILE = _APEX_MEMORY_DIR / "APEX_USER.md"
_MEMORY_LIMIT = 2200
_USER_LIMIT = 1375

_INJECTION_PATTERNS = _re.compile(
    r"ignore previous instructions|system prompt|<\|im_start\|>|password|api.?key|secret",
    _re.I,
)


def load_memory_files() -> dict:
    """Return {memory: str, user: str} — empty strings if files don't exist yet."""
    _APEX_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    m = _MEMORY_FILE.read_text() if _MEMORY_FILE.exists() else ""
    u = _USER_FILE.read_text() if _USER_FILE.exists() else ""
    return {"memory": m.strip(), "user": u.strip()}


def save_memory_entry(
    target: str,
    action: str,
    content: str,
    old_text: str = None,
    _bypass_approval: bool = False,
) -> str:
    """Mutate one of the memory files. Returns a human-readable result string."""
    # Write-approval gate: stage instead of applying when enabled.
    if not _bypass_approval:
        try:
            import config as _cfg
            approval_on = getattr(_cfg, "MEMORY_WRITE_APPROVAL", False)
        except Exception:
            approval_on = False
        if approval_on:
            # Fail CLOSED: if staging errors, do NOT fall through to the direct write.
            from agent import approvals as _appr
            try:
                return _appr.stage("memory", {
                    "target": target, "action": action,
                    "content": content, "old_text": old_text,
                })
            except Exception as e:
                return f"Write blocked: approval is required but staging failed ({e})."

    path = _MEMORY_FILE if target == "memory" else _USER_FILE
    limit = _MEMORY_LIMIT if target == "memory" else _USER_LIMIT

    # Both add and replace write attacker-influenceable content that is later read
    # into the system prompt — screen the injected text on both paths.
    if action in ("add", "replace") and _INJECTION_PATTERNS.search(content or ""):
        return "Rejected: content matches security pattern."

    _APEX_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    current = path.read_text().strip() if path.exists() else ""

    if action == "add":
        entry = (content or "").strip()
        if not entry:
            return "No content to add."
        if entry in current:
            return "Duplicate — entry already exists."
        separator = " § " if current else ""
        proposed = (current + separator + entry).strip()
        if len(proposed) > limit:
            parts = [p.strip() for p in current.split("§") if p.strip()]
            while parts and len(" § ".join(parts) + " § " + entry) > limit:
                parts.pop(0)
            proposed = (" § ".join(parts) + (" § " if parts else "") + entry).strip(" §")
        path.write_text(proposed)
        return f"Added. [{len(proposed)}/{limit} chars used]"

    if action == "replace":
        if not old_text:
            return "old_text required for replace."
        if old_text not in current:
            return f"Not found: {old_text!r}"
        proposed = current.replace(old_text, (content or "").strip(), 1).strip()
        if len(proposed) > limit:
            return f"Too long after replace: {len(proposed)}/{limit} chars."
        path.write_text(proposed)
        return "Replaced."

    if action == "remove":
        if not old_text:
            return "old_text required for remove."
        if old_text not in current:
            return f"Not found: {old_text!r}"
        parts = [p.strip() for p in current.split("§") if p.strip() and old_text not in p]
        path.write_text(" § ".join(parts))
        return "Removed."

    return f"Unknown action: {action!r}"
