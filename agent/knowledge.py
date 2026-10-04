"""Knowledge base: RAG over the user's actual files.

Walks user-configured paths, chunks text/markdown/code/PDF files,
embeds with the same all-MiniLM-L6-v2 used for memories, stores in
SQLite (alongside long-term memory DB). Search returns top-K relevant
chunks with file paths.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import numpy as np

from agent import doc_convert, longterm

CHUNK_SIZE = 800        # characters
CHUNK_OVERLAP = 120
ALLOWED_EXTS = {
    ".md", ".txt", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".html", ".css", ".sh", ".yaml", ".yml", ".json", ".toml",
    ".pdf", ".docx", ".pptx", ".xlsx",   # through agent/doc_convert.py
}
MAX_FILE_BYTES = 2_000_000  # skip huge files


def init_db():
    """Ensure the kb_chunks table exists in the long-term memory DB."""
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB,
                mtime REAL,
                indexed_at REAL,
                UNIQUE(path, chunk_index)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_kb_path ON kb_chunks(path)")


def _size_ok(path: Path) -> bool:
    """Documents are mostly zip and images, so they get doc_convert's limit."""
    limit = doc_convert.MAX_BYTES if path.suffix.lower() in doc_convert.CONVERTIBLE else MAX_FILE_BYTES
    return path.stat().st_size <= limit


def _read_file(path: Path) -> Optional[str]:
    try:
        if path.suffix.lower() in doc_convert.CONVERTIBLE:
            result = doc_convert.convert(path)
            return result["text"] if result["status"] != "needs_ocr" else None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _chunk(text: str) -> list[str]:
    chunks = []
    i = 0
    n = len(text)
    while i < n:
        end = min(i + CHUNK_SIZE, n)
        # Try to break on newline near the end
        if end < n:
            nl = text.rfind("\n", i + CHUNK_SIZE // 2, end)
            if nl > i:
                end = nl
        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        i = end - CHUNK_OVERLAP if end < n else end
        if i < 0:
            i = end
    return chunks


def reindex(paths: list[str], force: bool = False) -> str:
    """Walk the given paths, (re)index any text files found."""
    init_db()
    model = longterm._get_embed_model()
    if model is None:
        return "Embedding model unavailable. Cannot index — check internet on first run."

    indexed = 0
    skipped = 0
    deleted = 0
    expanded_paths = [Path(p).expanduser() for p in paths]

    for root in expanded_paths:
        if not root.exists():
            continue
        if root.is_file():
            files = [root]
        else:
            files = []
            for sub in root.rglob("*"):
                if sub.is_file() and sub.suffix.lower() in ALLOWED_EXTS:
                    if _size_ok(sub):
                        files.append(sub)

        for f in files:
            try:
                mtime = f.stat().st_mtime
            except Exception:
                continue

            with longterm._conn() as c:
                row = c.execute(
                    "SELECT MIN(mtime) FROM kb_chunks WHERE path = ?", (str(f),)
                ).fetchone()
                existing_mtime = row[0] if row else None

            if (not force) and existing_mtime is not None and existing_mtime >= mtime:
                skipped += 1
                continue

            text = _read_file(f)
            if not text or len(text.strip()) < 20:
                continue

            chunks = _chunk(text)
            if not chunks:
                continue

            embeddings = model.encode(chunks, normalize_embeddings=True, show_progress_bar=False)

            with longterm._conn() as c:
                c.execute("DELETE FROM kb_chunks WHERE path = ?", (str(f),))
                for idx, (chunk, vec) in enumerate(zip(chunks, embeddings)):
                    c.execute(
                        "INSERT INTO kb_chunks (path, chunk_index, content, embedding, mtime, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(f), idx, chunk, vec.astype(np.float32).tobytes(), mtime, time.time()),
                    )
                indexed += 1

    return f"Reindex complete. {indexed} files indexed, {skipped} skipped (unchanged), {deleted} removed."


def remove_path(path: str) -> int:
    """Remove all chunks for a path (when file deleted)."""
    with longterm._conn() as c:
        cur = c.execute("DELETE FROM kb_chunks WHERE path = ?", (path,))
        return cur.rowcount


def search(query: str, top_k: int = 6) -> list[dict]:
    """Cosine-similarity search across all indexed chunks."""
    init_db()
    model = longterm._get_embed_model()
    if model is None:
        return [{"error": "Embedding model unavailable."}]

    query_vec = model.encode([query], normalize_embeddings=True)[0].astype(np.float32)

    with longterm._conn() as c:
        rows = c.execute(
            "SELECT path, chunk_index, content, embedding FROM kb_chunks"
        ).fetchall()

    if not rows:
        return []

    scores = []
    for path, idx, content, emb_blob in rows:
        if emb_blob is None:
            continue
        emb = np.frombuffer(emb_blob, dtype=np.float32)
        scores.append((float(np.dot(query_vec, emb)), path, idx, content))

    scores.sort(reverse=True)
    return [
        {"score": round(s, 4), "path": p, "chunk_index": i, "content": c}
        for s, p, i, c in scores[:top_k]
    ]


def stats() -> dict:
    init_db()
    with longterm._conn() as c:
        row = c.execute("SELECT COUNT(DISTINCT path), COUNT(*) FROM kb_chunks").fetchone()
    return {"files": row[0] if row else 0, "chunks": row[1] if row else 0}
