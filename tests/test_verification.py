"""Tests for work verification (agent/verification.py + the goals.update_goal gate).

The point of this module is that it CANNOT be talked into saying yes, so most of
these tests are about refusing to pass rather than about passing.
"""
import pytest

from agent import verification as v, goals, longterm


def _mk_goal(title="ship the thing"):
    goals.init_db()
    v.init_db()
    goals.set_goal(title)
    with longterm._conn() as c:
        return c.execute("SELECT id FROM goals ORDER BY id DESC LIMIT 1").fetchone()[0]


# --- contracts ---------------------------------------------------------------

def test_add_contract_validates_kind(test_db):
    gid = _mk_goal()
    assert "Invalid contract kind" in v.add_contract(gid, "vibes", "looks done")
    assert "Contract #" in v.add_contract(gid, "file_exists", "/tmp/x")


def test_add_contract_requires_spec(test_db):
    gid = _mk_goal()
    assert "needs a spec" in v.add_contract(gid, "command", "   ")


# --- individual checks -------------------------------------------------------

def test_file_exists_check(test_db, tmp_path):
    gid = _mk_goal()
    p = tmp_path / "out.txt"
    v.add_contract(gid, "file_exists", str(p))
    assert v.verify(gid)["passed"] is False        # missing
    p.write_text("")
    assert v.verify(gid)["passed"] is False        # exists but EMPTY is not done
    p.write_text("real content")
    assert v.verify(gid)["passed"] is True


def test_contains_check(test_db, tmp_path):
    gid = _mk_goal()
    p = tmp_path / "report.md"
    p.write_text("# Draft\nnothing yet")
    v.add_contract(gid, "contains", str(p), detail="CONCLUSION")
    assert v.verify(gid)["passed"] is False
    p.write_text("# Draft\nCONCLUSION: done")
    assert v.verify(gid)["passed"] is True


def test_manual_never_self_passes(test_db):
    gid = _mk_goal()
    v.add_contract(gid, "manual", "human must sign off")
    res = v.verify(gid)
    assert res["passed"] is False
    assert "human" in res["results"][0]["evidence"].lower()


def test_command_without_sandbox_fails_closed(test_db, monkeypatch):
    """A command check must NEVER fall back to host execution."""
    gid = _mk_goal()
    v.add_contract(gid, "command", "echo pwned")
    from tools import sandbox
    def _raise(refresh=False):
        raise sandbox.SandboxUnavailable("no docker")
    monkeypatch.setattr(sandbox, "autonomous_backend", _raise)
    res = v.verify(gid)
    assert res["passed"] is False
    assert "sandbox unavailable" in res["results"][0]["evidence"].lower()


def test_command_uses_exit_code(test_db, monkeypatch):
    gid = _mk_goal()
    v.add_contract(gid, "command", "true")
    from tools import sandbox
    monkeypatch.setattr(sandbox, "autonomous_backend",
                        lambda refresh=False: sandbox.LocalBackend())
    assert v.verify(gid)["passed"] is True

    gid2 = _mk_goal("failing goal")
    v.add_contract(gid2, "command", "exit 3")
    assert v.verify(gid2)["passed"] is False


# --- no contract = backward compatible ---------------------------------------

def test_no_contract_is_not_contracted(test_db):
    gid = _mk_goal()
    res = v.verify(gid)
    assert res["contracted"] is False and res["passed"] is True


# --- the gate in update_goal -------------------------------------------------

def test_gate_blocks_closing_a_failing_goal(test_db, tmp_path):
    gid = _mk_goal()
    v.add_contract(gid, "file_exists", str(tmp_path / "never.txt"))
    out = goals.update_goal(gid, status="done")
    assert "NOT closed" in out
    with longterm._conn() as c:
        status = c.execute("SELECT status FROM goals WHERE id=?", (gid,)).fetchone()[0]
    assert status == "active"          # genuinely not closed


def test_gate_allows_closing_a_passing_goal(test_db, tmp_path):
    gid = _mk_goal()
    p = tmp_path / "done.txt"; p.write_text("yes")
    v.add_contract(gid, "file_exists", str(p))
    out = goals.update_goal(gid, status="done")
    assert "verified" in out
    with longterm._conn() as c:
        assert c.execute("SELECT status FROM goals WHERE id=?", (gid,)).fetchone()[0] == "done"


def test_uncontracted_goal_still_closes_but_marked_unverified(test_db):
    gid = _mk_goal()
    out = goals.update_goal(gid, status="done")
    assert "unverified" in out
    with longterm._conn() as c:
        assert c.execute("SELECT status FROM goals WHERE id=?", (gid,)).fetchone()[0] == "done"


def test_force_override_is_recorded(test_db, tmp_path):
    gid = _mk_goal()
    v.add_contract(gid, "file_exists", str(tmp_path / "never.txt"))
    out = goals.update_goal(gid, status="done", _force=True)
    assert "FORCE-CLOSED" in out


def test_gate_fails_closed_if_verification_itself_breaks(test_db, monkeypatch):
    """If the gate can't run, the goal must NOT close."""
    gid = _mk_goal()
    import agent.verification as verif
    monkeypatch.setattr(verif, "verify",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate broken")))
    out = goals.update_goal(gid, status="done")
    assert "NOT closed" in out
    with longterm._conn() as c:
        assert c.execute("SELECT status FROM goals WHERE id=?", (gid,)).fetchone()[0] == "active"


def test_non_done_status_is_not_gated(test_db, tmp_path):
    gid = _mk_goal()
    v.add_contract(gid, "file_exists", str(tmp_path / "never.txt"))
    out = goals.update_goal(gid, status="paused")   # only 'done' is gated
    assert "NOT closed" not in out


def test_missing_contracts_table_does_not_block_closing(test_db):
    """Regression: an older DB with no goal_contracts table must still close goals.

    A missing table provably means zero contracts, so it is 'uncontracted', not a
    verification failure. Getting this wrong blocked EVERY goal close.
    """
    goals.init_db()
    goals.set_goal("legacy goal")
    with longterm._conn() as c:
        gid = c.execute("SELECT id FROM goals ORDER BY id DESC LIMIT 1").fetchone()[0]
        c.execute("DROP TABLE IF EXISTS goal_contracts")
    assert v.list_contracts(gid) == []
    assert v.verify(gid)["contracted"] is False


def test_other_db_errors_still_fail_closed(test_db, monkeypatch):
    """Only 'no such table' is forgiven — anything else must still fail closed."""
    gid = _mk_goal()
    def boom():
        raise RuntimeError("disk exploded")
    monkeypatch.setattr(longterm, "_conn", lambda: boom())
    with pytest.raises(Exception):
        v.list_contracts(gid)


def test_verification_is_logged(test_db, tmp_path):
    gid = _mk_goal()
    v.add_contract(gid, "file_exists", str(tmp_path / "nope.txt"))
    v.verify(gid)
    hist = v.history(gid)
    assert hist and hist[0]["passed"] is False
