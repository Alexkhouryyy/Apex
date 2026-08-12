"""Tests for trajectory capture (agent/trajectory.py)."""
import time

from agent import trajectory as traj


def test_classify_taxonomy():
    assert traj.classify("Unknown tool: frobnicate")[0] == traj.UNKNOWN_TOOL
    assert traj.classify("[BLOCKED by safety layer] recursive delete")[0] == traj.BLOCKED
    assert traj.classify("Traceback (most recent call last): ...")[0] == traj.ERROR
    assert traj.classify("Command timed out after 30s")[0] == traj.ERROR
    assert traj.classify("EMAIL_SMTP_HOST not configured")[0] == traj.ERROR
    assert traj.classify("Here are your results: 42")[0] == traj.OK


def test_classify_error_kinds():
    assert traj.classify("Unknown tool: x")[1] == "no_such_tool"
    assert traj.classify("[BLOCKED by safety layer] y")[1] == "safety_gate"
    assert traj.classify("Command timed out after 30s")[1] == "timeout"


def test_record_persists_outcome(test_db):
    traj.init_db()
    assert traj.record("web_search", "results here", duration_ms=120) == traj.OK
    assert traj.record("bash", "Traceback (most recent call last):") == traj.ERROR
    rows = traj.recent()
    assert len(rows) == 2
    tools = {r["tool"] for r in rows}
    assert tools == {"web_search", "bash"}


def test_record_never_stores_input_values(test_db):
    traj.init_db()
    traj.record("email_draft", "ok", inputs={"to": "secret@example.com", "body": "sensitive"})
    from agent import longterm
    with longterm._conn() as c:
        keys = c.execute("SELECT input_keys FROM tool_events ORDER BY id DESC LIMIT 1").fetchone()[0]
    # Key NAMES are kept; values must never be.
    assert "to" in keys and "body" in keys
    assert "secret@example.com" not in keys and "sensitive" not in keys


def test_recovery_is_marked(test_db):
    traj.init_db()
    traj.record("bash", "Traceback (most recent call last):")
    traj.record("bash", "done")  # a later success closes out the failure
    from agent import longterm
    with longterm._conn() as c:
        recovered = c.execute(
            "SELECT recovered FROM tool_events WHERE outcome != ? ORDER BY id LIMIT 1", (traj.OK,)
        ).fetchone()[0]
    assert recovered == 1


def test_failure_of_other_tool_is_not_recovered(test_db):
    traj.init_db()
    traj.record("bash", "Traceback (most recent call last):")
    traj.record("web_search", "fine")  # different tool must NOT mark bash recovered
    from agent import longterm
    with longterm._conn() as c:
        recovered = c.execute(
            "SELECT recovered FROM tool_events WHERE tool = 'bash'"
        ).fetchone()[0]
    assert recovered == 0


def test_stats_aggregates(test_db):
    traj.init_db()
    traj.record("a", "ok")
    traj.record("b", "Unknown tool: b")
    traj.record("b", "Unknown tool: b")
    s = traj.stats()
    assert s["total"] == 3
    assert s["by_outcome"][traj.UNKNOWN_TOOL] == 2
    assert s["worst_tools"][0]["tool"] == "b"
    assert 0 <= s["success_rate"] <= 100


def test_record_never_raises(test_db, monkeypatch):
    # Capture must never break a tool call, even if the DB is broken.
    def boom(*a, **k):
        raise RuntimeError("db gone")
    from agent import longterm
    monkeypatch.setattr(longterm, "_conn", boom)
    assert traj.record("x", "ok") == ""  # degrades silently
