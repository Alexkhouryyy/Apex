"""Threads — a serendipity engine over Apex's memory embeddings.

Apex already embeds every memory for semantic recall, but those vectors are only
ever used *reactively* (when you ask). Threads uses them *proactively* to surface
non-obvious connections: pairs of memories that are semantically close but come
from DIFFERENT domains (a work note that quietly rhymes with a personal goal),
sitting in a "sweet spot" of similarity — related enough to be meaningful, not so
close they're near-duplicates.

Core is pure cosine math over the existing `memories.embedding` blobs (normalized
float32), so it costs nothing and is fully deterministic/testable. A small
`threads_surfaced` table remembers what's been shown so "thread of the day" never
repeats and can learn from your useful/dismiss reactions.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from agent import longterm

# Similarity band for a "non-obvious but real" link. Below LO = unrelated noise;
# above HI = basically the same memory (not insightful). Cross-domain pairs inside
# this band are the interesting ones.
_SIM_LO = 0.35
_SIM_HI = 0.85
_CANDIDATE_CAP = 300  # bound the O(n^2) pair scan to the most important memories


_ready = False


def _ensure_db() -> None:
    """Create tables on first use.

    threads.init_db() was never called from any entry point, so
    `threads_surfaced` never existed and every surfacing attempt failed against
    a missing table. Same shape as the restraint bug: nothing crashed loudly, it
    simply never worked.
    """
    global _ready
    if _ready:
        return
    try:
        init_db()
        _ready = True
    except Exception:
        pass


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS threads_surfaced (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                a_id     INTEGER NOT NULL,
                b_id     INTEGER NOT NULL,
                score    REAL NOT NULL,
                ts       REAL NOT NULL,
                reaction TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_threads_pair ON threads_surfaced(a_id, b_id)")


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _load(cap: int = _CANDIDATE_CAP) -> list[dict]:
    """Most-important/recent memories that actually have an embedding."""
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, kind, content, importance, embedding FROM memories "
            "WHERE embedding IS NOT NULL ORDER BY importance DESC, ts DESC LIMIT ?",
            (cap,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            vec = np.frombuffer(r[4], dtype=np.float32)
        except Exception:
            continue
        if vec.size == 0:
            continue
        out.append({"id": r[0], "kind": r[1], "content": r[2], "importance": r[3], "vec": vec})
    return out


def related(memory_id: int, k: int = 5) -> list[dict]:
    """Top-k semantic neighbours of one memory (excluding itself)."""
    mems = _load(cap=2000)
    target = next((m for m in mems if m["id"] == memory_id), None)
    if target is None:
        return []
    scored = []
    for m in mems:
        if m["id"] == memory_id:
            continue
        scored.append((float(np.dot(target["vec"], m["vec"])), m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"id": m["id"], "kind": m["kind"], "content": m["content"], "score": round(s, 3)}
        for s, m in scored[:k]
    ]


def discover(limit: int = 20, cross_domain_only: bool = True) -> list[dict]:
    """Find the most interesting non-obvious links: cross-domain pairs whose
    similarity sits in the meaningful band. Deterministic (sorted by score)."""
    mems = _load()
    n = len(mems)
    pairs = []
    for i in range(n):
        vi, ki = mems[i]["vec"], mems[i]["kind"]
        for j in range(i + 1, n):
            if cross_domain_only and mems[j]["kind"] == ki:
                continue
            s = float(np.dot(vi, mems[j]["vec"]))
            if _SIM_LO <= s <= _SIM_HI:
                pairs.append((s, mems[i], mems[j]))
    pairs.sort(key=lambda t: t[0], reverse=True)
    return [
        {
            "a": {"id": a["id"], "kind": a["kind"], "content": a["content"]},
            "b": {"id": b["id"], "kind": b["kind"], "content": b["content"]},
            "score": round(s, 3),
        }
        for s, a, b in pairs[:limit]
    ]


def _already_surfaced() -> set[tuple[int, int]]:
    _ensure_db()
    with longterm._conn() as c:
        rows = c.execute("SELECT a_id, b_id FROM threads_surfaced").fetchall()
    return {_pair_key(r[0], r[1]) for r in rows}


def surface_next() -> Optional[dict]:
    _ensure_db()
    """Pick the strongest cross-domain link not yet shown, record it, return it."""
    seen = _already_surfaced()
    for cand in discover(limit=50):
        key = _pair_key(cand["a"]["id"], cand["b"]["id"])
        if key in seen:
            continue
        with longterm._conn() as c:
            cur = c.execute(
                "INSERT INTO threads_surfaced (a_id, b_id, score, ts) VALUES (?, ?, ?, ?)",
                (cand["a"]["id"], cand["b"]["id"], cand["score"], time.time()),
            )
            cand["thread_id"] = cur.lastrowid
        return cand
    return None


def react(thread_id: int, reaction: str) -> bool:
    """Record 'useful' or 'dismiss' so surfacing can improve over time."""
    if reaction not in ("useful", "dismiss"):
        return False
    with longterm._conn() as c:
        cur = c.execute(
            "UPDATE threads_surfaced SET reaction = ? WHERE id = ?", (reaction, int(thread_id))
        )
        return cur.rowcount > 0


def recent(limit: int = 20) -> list[dict]:
    """Recently surfaced threads (with content + reaction) for the dashboard."""
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, a_id, b_id, score, ts, reaction FROM threads_surfaced ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        contents = {}
        if rows:
            ids = {r[1] for r in rows} | {r[2] for r in rows}
            q = ",".join("?" * len(ids))
            for mid, kind, content in c.execute(
                f"SELECT id, kind, content FROM memories WHERE id IN ({q})", tuple(ids)
            ).fetchall():
                contents[mid] = {"kind": kind, "content": content}
    out = []
    for tid, a_id, b_id, score, ts, reaction in rows:
        out.append({
            "thread_id": tid, "score": score, "ts": ts, "reaction": reaction,
            "a": contents.get(a_id, {"content": "(memory deleted)"}) | {"id": a_id},
            "b": contents.get(b_id, {"content": "(memory deleted)"}) | {"id": b_id},
        })
    return out


def stats() -> dict:
    with longterm._conn() as c:
        total = c.execute("SELECT COUNT(*) FROM threads_surfaced").fetchone()[0]
        useful = c.execute("SELECT COUNT(*) FROM threads_surfaced WHERE reaction='useful'").fetchone()[0]
    return {"surfaced": total, "useful": useful,
            "hit_rate": round(100 * useful / total) if total else 0}
