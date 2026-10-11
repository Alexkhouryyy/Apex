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
from contextlib import closing
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


def default_db() -> Path:
    return Path(os.path.expanduser(
        os.getenv("DB_PATH", "~/.voice_agent_memory.db")))


def backup(src: Path, dest: Path) -> dict:
    if not src.exists():
        raise FileNotFoundError(f"no database at {src}")
    if src.resolve() == dest.resolve() or dest.exists() and src.samefile(dest):
        raise ValueError('The backup destination must not be the source database or its alias.')

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the same filesystem. Keep the last good backup until its
    # replacement has been verified and every SQLite handle is closed.
    with tempfile.TemporaryDirectory(prefix='.apex-backup-', dir=dest.parent) as tmp:
        staged = Path(tmp) / 'backup.db'
        source_uri = f'file:{quote(str(src.resolve()), safe="/:")}?mode=ro'
        with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source, \
                closing(sqlite3.connect(str(staged))) as target:
            source.backup(target)

        staged_uri = f'file:{quote(str(staged.resolve()), safe="/:")}?mode=ro'
        with closing(sqlite3.connect(staged_uri, uri=True)) as c:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {}
            for table in ("memories", "turn_log", "sessions", "entities",
                          "goals", "reflections", "study_projects", "board_workspaces", "workspace_cards", "workspace_studies"):
                try:
                    counts[table] = c.execute(
                        f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                except sqlite3.OperationalError:
                    counts[table] = None
        if integrity != "ok":
            raise RuntimeError(f"backup failed integrity check: {integrity}")
        info = {"bytes": staged.stat().st_size, "counts": counts}
        staged.replace(dest)
        return info


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
