"""Semantic retrieval over the Obsidian vault.

`agent/vault.py` writes Markdown notes and reads them back — but only by title.
`list_notes()` and `read_note(title)` both require you to already know which
page you want, which is the one thing the blueprint's Phase 2 success check
says you should not need to know:

    "Apex retrieves the correct project page WITHOUT loading the full vault."

Both halves of that sentence matter, and the second is the harder one. Reading
every note and picking the best is not retrieval, it is a linear scan wearing a
hat: it works on twelve notes, costs real money and latency on twelve hundred,
and there is no point at which it visibly stops being fine. So the rule this
module is built around, and the one `tests/test_vault_index.py` enforces by
making file reads throw:

    **search() opens no note files.** Everything it answers with — title,
    folder, excerpt, score — comes out of the index.

Indexing reads the files. Querying does not.

## Why a content hash rather than mtime

Re-indexing everything on every boot would make the embedding model the cost of
starting Apex. So only changed notes are re-embedded, and "changed" is decided
by hashing the bytes, not by comparing mtime and size.

That is not caution for its own sake: this session already lost an hour to
Python's own mtime+size bytecode cache, which happily served a stale `.pyc`
after a same-length edit within one second. A vault sits in a folder a human
edits in Obsidian and a sync client rewrites; same-size edits and reset
timestamps are ordinary there, and a stale index is worse than no index because
it answers confidently.

## Three ways this could fail quietly, and what each does instead

  * **No embedding model.** `longterm._embed` returns None and says so once.
    An index that silently held no vectors would make every search return
    nothing, which reads exactly like "you have no notes about that". Search
    falls back to keyword matching over titles and excerpts, and every result
    says `matched: "keyword"` so the degraded mode is visible in the answer
    rather than inferred from its quality.

  * **A note deleted in Obsidian.** Its row has to go, or search keeps offering
    a page that is not there — a retrieval that "works" and points at nothing.

  * **A vault that was never indexed.** `search()` returns a stated reason, not
    an empty list. Empty is the same shape as "nothing matched", and those two
    need completely different fixes.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Optional

import numpy as np

from agent import longterm, vault

# How much of each note is kept for display and for the keyword fallback.
# Enough to recognise the note; not so much that the index becomes a second
# copy of the vault.
EXCERPT_CHARS = 400

# Frontmatter is metadata, not prose. Left in, every note would begin with the
# same six lines and the embeddings would all be a little bit alike.
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS vault_index (
                path TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                excerpt TEXT NOT NULL DEFAULT '',
                embedding BLOB,
                indexed_at REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_vault_folder ON vault_index(folder)")


def strip_frontmatter(text: str) -> str:
    """Body only. Wikilinks keep their target text — `[[Phone Stand|it]]`
    carries the words "Phone Stand", which is exactly what a search for a phone
    stand should match on."""
    body = _FRONTMATTER.sub("", text or "", count=1)
    return _WIKILINK.sub(r"\1", body).strip()


def excerpt_of(body: str) -> str:
    flat = " ".join((body or "").split())
    return flat[:EXCERPT_CHARS]


def hash_of(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:32]


def embed_text(title: str, body: str) -> Optional[bytes]:
    """The title is prepended to the body before embedding.

    A note called "Phone stand" whose body never repeats the phrase is still
    about a phone stand, and the filename is the strongest signal a person
    gives about what a note is for.
    """
    return longterm._embed(f"{title}\n\n{body}"[:8000])


def _vault_files() -> list[Path]:
    root = vault.VAULT_DIR
    if not root.exists():
        return []
    return [p for p in sorted(root.rglob("*.md")) if ".obsidian" not in p.parts]


def reindex(*, force: bool = False) -> dict:
    """Bring the index in step with the vault. Returns what it did.

    Counts rather than a bare boolean, because "indexed 0 notes" has three very
    different causes — an empty vault, an unchanged vault, and a vault whose
    every note failed to read — and a caller that cannot tell them apart will
    report the wrong one.
    """
    init_db()
    seen: set[str] = set()
    added = updated = unchanged = failed = 0
    no_model = 0

    with longterm._conn() as c:
        known = {r[0]: r[1] for r in c.execute(
            "SELECT path, content_hash FROM vault_index")}

        for p in _vault_files():
            try:
                rel = str(p.relative_to(vault.VAULT_DIR))
                raw = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                failed += 1
                continue
            seen.add(rel)
            digest = hash_of(raw)
            if not force and known.get(rel) == digest:
                unchanged += 1
                continue

            body = strip_frontmatter(raw)
            title = p.stem
            vec = embed_text(title, body)
            if vec is None:
                no_model += 1
            c.execute(
                "INSERT INTO vault_index (path, title, folder, content_hash,"
                " excerpt, embedding, indexed_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET title=excluded.title,"
                " folder=excluded.folder, content_hash=excluded.content_hash,"
                " excerpt=excluded.excerpt, embedding=excluded.embedding,"
                " indexed_at=excluded.indexed_at",
                (rel, title, p.parent.name, digest, excerpt_of(body), vec, time.time()))
            if rel in known:
                updated += 1
            else:
                added += 1

        # Notes deleted in Obsidian. Without this, search keeps offering a page
        # that is not there — a retrieval that "works" and points at nothing.
        removed = 0
        for rel in known:
            if rel not in seen:
                c.execute("DELETE FROM vault_index WHERE path = ?", (rel,))
                removed += 1
        c.commit()

    return {"added": added, "updated": updated, "unchanged": unchanged,
            "removed": removed, "failed": failed, "without_embedding": no_model,
            "total": added + updated + unchanged}


def index_note(path: Path) -> bool:
    """Index one note now. Used when Apex writes a note itself, so its own
    writing is searchable immediately rather than at the next full pass."""
    init_db()
    try:
        rel = str(Path(path).resolve().relative_to(vault.VAULT_DIR.resolve()))
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    body = strip_frontmatter(raw)
    title = Path(path).stem
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO vault_index (path, title, folder, content_hash,"
            " excerpt, embedding, indexed_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET title=excluded.title,"
            " folder=excluded.folder, content_hash=excluded.content_hash,"
            " excerpt=excluded.excerpt, embedding=excluded.embedding,"
            " indexed_at=excluded.indexed_at",
            (rel, title, Path(path).parent.name, hash_of(raw), excerpt_of(body),
             embed_text(title, body), time.time()))
        c.commit()
    return True


def forget_note(path: Path) -> None:
    try:
        rel = str(Path(path).resolve().relative_to(vault.VAULT_DIR.resolve()))
    except Exception:
        return
    try:
        with longterm._conn() as c:
            c.execute("DELETE FROM vault_index WHERE path = ?", (rel,))
            c.commit()
    except Exception:
        pass


def _staleness_note(indexed: int, folder: Optional[str]) -> str:
    """A warning built from a directory LISTING, never from reading notes.

    Counting filenames is a glob; it does not put the vault's contents back on
    the retrieval path. This exists because an index quietly one note behind
    answers confidently with the wrong page, and nothing about the answer says
    so — the failure mode this whole module was written against.
    """
    if folder:
        return ""      # the count would compare a subset against the whole vault
    try:
        on_disk = len(_vault_files())
    except Exception:
        return ""
    if on_disk == indexed:
        return ""
    return (f" The index may be stale: {on_disk} notes on disk, {indexed} "
            f"indexed. Reindex to search the rest.")


def search(query: str, limit: int = 5, folder: Optional[str] = None) -> dict:
    """Find notes by meaning. Reads the index; never opens a note file.

    Returns a dict, not a bare list, because the interesting failures here are
    all "no results, but for very different reasons" — an unindexed vault, a
    missing embedding model, a genuine miss — and a list flattens them into one
    empty answer.
    """
    init_db()
    q = (query or "").strip()
    if not q:
        return {"results": [], "mode": "none", "note": "No query given."}

    sql = ("SELECT path, title, folder, excerpt, embedding FROM vault_index"
           + (" WHERE folder = ?" if folder else ""))
    args = (folder,) if folder else ()
    with longterm._conn() as c:
        rows = c.execute(sql, args).fetchall()

    if not rows:
        with longterm._conn() as c:
            any_row = c.execute("SELECT 1 FROM vault_index LIMIT 1").fetchone()
        if any_row:
            return {"results": [], "mode": "semantic",
                    "note": f"Nothing is indexed in the '{folder}' folder."}
        return {"results": [], "mode": "unindexed",
                "note": ("The vault has never been indexed, so there is nothing "
                         "to search. Run vault_index.reindex(). This is not the "
                         "same as having no matching notes.")}

    model = longterm._get_embed_model()
    have_vectors = any(r[4] is not None for r in rows)

    if model is not None and have_vectors:
        qv = model.encode([q], normalize_embeddings=True)[0].astype(np.float32)
        scored = []
        for path, title, fold, exc, blob in rows:
            if blob is None:
                continue      # indexed before the model was available
            score = float(np.dot(qv, np.frombuffer(blob, dtype=np.float32)))
            scored.append({"path": path, "title": title, "folder": fold,
                           "excerpt": exc, "score": round(score, 4),
                           "matched": "semantic"})
        scored.sort(key=lambda r: r["score"], reverse=True)
        out = {"results": scored[:limit], "mode": "semantic",
               "note": f"{len(scored)} notes searched by meaning."}
        out["note"] += _staleness_note(len(rows), folder)
        missing = sum(1 for r in rows if r[4] is None)
        if missing:
            # Said out loud: those notes cannot be found by meaning at all, and
            # a partially-vectorised index that stays quiet about it looks
            # exactly like a complete one.
            out["note"] += (f" {missing} note(s) have no embedding and were not "
                            f"searched — reindex(force=True) after the model loads.")
        return out

    # Keyword fallback. Every result says so, because a degraded mode you have
    # to infer from the quality of the answers is one nobody ever notices.
    terms = [t for t in re.split(r"\W+", q.lower()) if len(t) > 2]
    scored = []
    for path, title, fold, exc, _blob in rows:
        hay = f"{title} {exc}".lower()
        hits = sum(1 for t in terms if t in hay)
        if hits:
            scored.append({"path": path, "title": title, "folder": fold,
                           "excerpt": exc, "score": hits, "matched": "keyword"})
    scored.sort(key=lambda r: r["score"], reverse=True)
    reason = ("the embedding model is unavailable" if model is None
              else "no note in the index has an embedding")
    return {"results": scored[:limit], "mode": "keyword",
            "note": (f"Keyword match only — {reason}, so these are word "
                     f"overlaps and not meaning. Install sentence-transformers "
                     f"for semantic search.")}


def start_background_reindex(*, log=print) -> "object":
    """Bring the index up to date on a daemon thread at boot.

    Deliberately NOT done inside `search()`. Freshness costs a read of every
    note, and doing that at query time would put the full vault back on the
    retrieval path — the exact thing Phase 2's success check rules out. So the
    reads happen once at startup, off the critical path, and `search()` keeps
    its promise never to open a note.

    Returns the thread so a caller can join it in a test. Failures are logged
    and never raised: a vault that cannot be indexed is a degraded search, and
    `status()` reports it; a vault that stops Apex booting is worse.
    """
    import threading

    def _run():
        try:
            r = reindex()
            if r["added"] or r["updated"] or r["removed"]:
                log(f"[Vault] Indexed {r['added']} new, {r['updated']} changed, "
                    f"{r['removed']} removed ({r['total']} notes searchable).")
            if r["without_embedding"]:
                log(f"[Vault] {r['without_embedding']} note(s) indexed without an "
                    f"embedding — semantic search will not find them. "
                    f"pip install sentence-transformers, then reindex.")
        except Exception as e:
            log(f"[Vault] Indexing failed, search will be stale or empty: {e}")

    t = threading.Thread(target=_run, daemon=True, name="VaultIndex")
    t.start()
    return t


def status() -> dict:
    """Whether the index agrees with the vault, for the dashboard and smoke.

    Reports the vault count and the indexed count separately rather than a
    single "ok". They disagree in two directions and the fixes differ: more
    files than rows means the index is stale, more rows than files means notes
    were deleted and nothing swept.
    """
    init_db()
    try:
        with longterm._conn() as c:
            rows = c.execute(
                "SELECT COUNT(*), SUM(embedding IS NOT NULL), MAX(indexed_at)"
                " FROM vault_index").fetchone()
    except Exception as e:
        return {"state": "unreadable", "detail": str(e)}
    indexed, vectored, last = rows[0] or 0, rows[1] or 0, rows[2] or 0.0
    on_disk = len(_vault_files())
    if indexed == 0:
        state = "empty" if on_disk == 0 else "never_indexed"
    elif indexed != on_disk:
        state = "stale"
    elif vectored < indexed:
        state = "partial_vectors"
    else:
        state = "ok"
    return {"state": state, "notes_on_disk": on_disk, "notes_indexed": indexed,
            "with_embeddings": vectored, "last_indexed_at": last,
            "vault_dir": str(vault.VAULT_DIR)}
