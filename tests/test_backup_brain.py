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

from contextlib import closing
import shutil
import sqlite3
import os

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
        with closing(sqlite3.connect(naive)) as c:
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
        with closing(sqlite3.connect(tmp_path / "safe.db")) as c:
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


def test_backup_releases_all_handles_before_returning(tmp_path, monkeypatch):
    source = tmp_path / 'live.db'
    writer = _live_db(source, rows=1)
    connect = sqlite3.connect
    opened = []

    def tracked(*args, **kwargs):
        conn = connect(*args, **kwargs)
        opened.append(conn)  # Keep references so GC cannot hide a leaked verifier.
        return conn

    monkeypatch.setattr(backup_brain.sqlite3, 'connect', tracked)
    try:
        dest = tmp_path / 'backup.db'
        assert backup_brain.backup(source, dest)['counts']['memories'] == 1
        assert len(opened) == 3
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError, match='closed'):
                conn.execute('SELECT 1')
        dest.unlink()
    finally:
        writer.close()


@pytest.mark.parametrize('alias', ['same-path', 'symlink', 'hard-link'])
def test_backup_refuses_source_aliases_without_changing_the_database(tmp_path, alias):
    src = tmp_path / 'brain.db'
    _live_db(src, rows=10).close()
    before = src.read_bytes()
    dest = src if alias == 'same-path' else tmp_path / 'alias.db'
    try:
        if alias == 'symlink':
            dest.symlink_to(src)
        elif alias == 'hard-link':
            os.link(src, dest)
    except OSError:
        pytest.skip('Filesystem does not permit this link')
    with pytest.raises(ValueError, match='source database'):
        backup_brain.backup(src, dest)
    assert src.read_bytes() == before
    assert dest.read_bytes() == before


@pytest.mark.parametrize('failure', ['copy', 'verification'])
def test_failed_backup_preserves_the_previous_copy(tmp_path, monkeypatch, failure):
    src, dest = tmp_path / 'brain.db', tmp_path / 'safe.db'
    writer = _live_db(src, rows=10)
    try:
        backup_brain.backup(src, dest)
        previous = dest.read_bytes()
        writer.execute("INSERT INTO memories (content) VALUES ('new work')")
        writer.commit()
        connect = sqlite3.connect

        class Connection(sqlite3.Connection):
            def backup(self, *args, **kwargs):
                if failure == 'copy':
                    raise sqlite3.OperationalError('simulated copy failure')
                return super().backup(*args, **kwargs)

            def execute(self, sql, *args, **kwargs):
                if failure == 'verification' and sql == 'PRAGMA integrity_check':
                    sql = "SELECT 'corrupt'"
                return super().execute(sql, *args, **kwargs)

        monkeypatch.setattr(backup_brain.sqlite3, 'connect',
                            lambda *a, **kw: connect(*a, factory=Connection, **kw))
        error = sqlite3.OperationalError if failure == 'copy' else RuntimeError
        with pytest.raises(error, match='copy failure|integrity check'):
            backup_brain.backup(src, dest)
        assert dest.read_bytes() == previous
        assert not list(tmp_path.glob('.apex-backup-*'))
    finally:
        writer.close()


def test_backup_handles_uri_characters_in_database_paths(tmp_path):
    folder = tmp_path / 'memory # archive'; folder.mkdir()
    src, dest = folder / 'brain #1.db', folder / 'safe #1.db'
    writer = _live_db(src, rows=3)
    try:
        assert backup_brain.backup(src, dest)['counts']['memories'] == 3
        with closing(sqlite3.connect(dest)) as conn:
            assert conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 3
    finally:
        writer.close()
