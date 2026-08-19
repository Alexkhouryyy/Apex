"""Backing up Apex's memory must not lose it.

The database runs in WAL mode, so it is not one file: recent writes sit in a
`-wal` sidecar until a checkpoint. While Apex is running and holding the
database open, copying `.voice_agent_memory.db` on its own produces a file that
raises "no such table: memories" — not a partial copy, an unusable one.

Apex is normally running (it autostarts as pythonw.exe), so that is the default
situation rather than an edge case, which is why the copy goes through SQLite's
online backup API instead of the filesystem.
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from scripts import backup_brain


def _live_db(path, rows=200):
    """A database held open by a writer, as a running Apex holds it."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
    conn.commit()
    for i in range(rows):
        conn.execute("INSERT INTO memories (content) VALUES (?)", (f"m{i}",))
    conn.commit()
    return conn


def test_a_plain_file_copy_of_a_live_db_is_unusable(tmp_path):
    """The reason this script exists, asserted rather than claimed."""
    src = tmp_path / "live.db"
    conn = _live_db(src)
    try:
        naive = tmp_path / "naive.db"
        shutil.copy(src, naive)
        with sqlite3.connect(naive) as c:
            with pytest.raises(sqlite3.OperationalError):
                c.execute("SELECT COUNT(*) FROM memories").fetchone()
    finally:
        conn.close()


def test_backup_captures_everything_while_apex_runs(tmp_path):
    src = tmp_path / "live.db"
    conn = _live_db(src, rows=200)
    try:
        info = backup_brain.backup(src, tmp_path / "safe.db")
        assert info["counts"]["memories"] == 200
        with sqlite3.connect(tmp_path / "safe.db") as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_a_missing_database_is_an_error_not_an_empty_file(tmp_path):
    """Silently writing an empty backup is how you discover the loss later."""
    with pytest.raises(FileNotFoundError):
        backup_brain.backup(tmp_path / "nope.db", tmp_path / "out.db")
    assert not (tmp_path / "out.db").exists()


def test_it_overwrites_a_previous_backup_cleanly(tmp_path):
    src = tmp_path / "live.db"
    conn = _live_db(src, rows=10)
    try:
        dest = tmp_path / "safe.db"
        backup_brain.backup(src, dest)
        conn.execute("INSERT INTO memories (content) VALUES ('later')")
        conn.commit()
        info = backup_brain.backup(src, dest)
        assert info["counts"]["memories"] == 11, "stale backup was left in place"
    finally:
        conn.close()
