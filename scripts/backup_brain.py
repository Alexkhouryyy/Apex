"""Make one clean, complete copy of Apex's memory.

Apex runs SQLite in WAL mode, so the database is not one file. Recent writes sit
in a `-wal` sidecar until a checkpoint folds them in. Copying only
`.voice_agent_memory.db` therefore silently drops the newest memories, and
copying it while Apex is running can produce a file that will not open at all.

This uses SQLite's online backup API, which produces a single consistent file
including everything in the WAL — and works while Apex is running, so nothing
has to be shut down first.

Usage:
    python scripts/backup_brain.py                     # -> apex-brain-backup.db
    python scripts/backup_brain.py --out D:/apex.db
    python scripts/backup_brain.py --db /path/to/other.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def default_db() -> Path:
    return Path(os.path.expanduser(
        os.getenv("DB_PATH", "~/.voice_agent_memory.db")))


def backup(src: Path, dest: Path) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"no database at {src}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    # Verify rather than assume: a backup nobody opened is a promise, not a copy.
    with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as c:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for table in ("memories", "turn_log", "sessions", "entities",
                      "goals", "reflections"):
            try:
                counts[table] = c.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = None
    if integrity != "ok":
        raise RuntimeError(f"backup failed integrity check: {integrity}")
    return {"bytes": dest.stat().st_size, "counts": counts}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Back up Apex's memory safely")
    ap.add_argument("--db", default=None, help="source database (default: Apex's)")
    ap.add_argument("--out", default="apex-brain-backup.db", help="where to write it")
    args = ap.parse_args(argv)

    src = Path(args.db) if args.db else default_db()
    dest = Path(args.out)
    try:
        info = backup(src, dest)
    except Exception as e:
        print(f"[Backup] FAILED: {e}", file=sys.stderr)
        return 1

    print(f"[Backup] {src}  ->  {dest.resolve()}")
    print(f"[Backup] {info['bytes']:,} bytes, integrity ok")
    for table, n in info["counts"].items():
        if n is not None:
            print(f"           {table:<12} {n:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
