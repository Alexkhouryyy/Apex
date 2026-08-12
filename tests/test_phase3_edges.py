"""Phase 3: goal-verification visibility, agent self-knowledge, bounded spills."""
import os
import time

import pytest

from agent import goals, verification as v, trajectory as traj, recovery as rec, longterm


def _mk_goal(title="ship it"):
    goals.init_db(); v.init_db()
    goals.set_goal(title)
    with longterm._conn() as c:
        return c.execute("SELECT id FROM goals ORDER BY id DESC LIMIT 1").fetchone()[0]


# --- 1. evidence must reach the verifier -------------------------------------

def test_evidence_reaches_the_llm_contract(test_db, monkeypatch):
    """The dashboard used to drop `evidence`, so an llm contract always saw
    '(none gathered)' and a correctly skeptical auditor always refused."""
    seen = {}
    gid = _mk_goal()
    v.add_contract(gid, "llm", "the report was written")

    from agent import provider
    def fake(model, system, user, max_tokens=200):
        seen["user"] = user
        return "PASS: the evidence describes a written report"
    monkeypatch.setattr(provider, "complete", fake)

    out = goals.update_goal(gid, status="done", evidence="I wrote report.md, 900 words")
    assert "I wrote report.md" in seen["user"]          # evidence actually arrived
    assert "(none gathered)" not in seen["user"]
    assert "NOT closed" not in out                       # and it could close


def test_missing_evidence_still_refuses(test_db, monkeypatch):
    """Guard the other direction: no evidence must still fail, not fail-open."""
    gid = _mk_goal()
    v.add_contract(gid, "llm", "the report was written")
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: "FAIL: no evidence of completion")
    out = goals.update_goal(gid, status="done")
    assert "NOT closed" in out


def test_verification_history_is_readable(test_db):
    """history() was written on every check and never read — the reason a close
    was refused was invisible."""
    gid = _mk_goal()
    v.add_contract(gid, "file_exists", "/definitely/not/here.txt")
    v.verify(gid)
    hist = v.history(gid)
    assert hist and hist[0]["passed"] is False
    assert v.list_contracts(gid)[0]["kind"] == "file_exists"


# --- 2. agent self-knowledge --------------------------------------------------

def _fail(tool, n):
    traj.init_db()
    for _ in range(n):
        traj.record(tool, "Traceback (most recent call last):")


def test_digest_is_silent_on_a_healthy_history(test_db):
    """Costs no prompt tokens when nothing is wrong."""
    traj.init_db()
    traj.record("web_search", "fine")
    assert traj.reliability_digest() == ""


def test_digest_names_a_genuinely_bad_tool(test_db):
    _fail("browser", 4)
    d = traj.reliability_digest()
    assert "browser" in d and "4 failures" in d


def test_digest_ignores_a_single_blip(test_db):
    """One failure is noise, not a pattern."""
    _fail("bash", 1)
    assert traj.reliability_digest() == ""


def test_digest_ignores_a_mostly_reliable_tool(test_db):
    """3 failures out of many calls is not 'unreliable'."""
    traj.init_db()
    _fail("bash", 3)
    for _ in range(30):
        traj.record("bash", "ok")
    assert "bash" not in traj.reliability_digest()


def test_digest_reaches_the_prompt_block(test_db):
    """It must ride along in active_goals_for_prompt, like the prefs digest."""
    goals.init_db()
    _fail("browser", 5)
    block = goals.active_goals_for_prompt()
    assert "Tool reliability warning" in block and "browser" in block


def test_repeat_failure_note_only_after_threshold(test_db):
    traj.init_db()
    traj.record("browser", "Traceback (most recent call last):")
    first = rec.enrich("browser", {}, "Traceback (most recent call last):")
    assert "has failed" not in first          # one blip: stay quiet

    _fail("browser", 4)
    later = rec.enrich("browser", {}, "Traceback (most recent call last):")
    assert "has failed" in later


def test_hint_does_not_change_how_the_outcome_is_recorded(test_db):
    """The trajectory signal must reflect the tool, not our advice."""
    traj.init_db()
    _fail("browser", 5)
    enriched = rec.enrich("browser", {}, "Traceback (most recent call last):")
    assert traj.classify(enriched)[0] == traj.ERROR


def test_success_is_never_annotated(test_db):
    traj.init_db()
    _fail("browser", 5)
    assert rec.enrich("browser", {}, "all good") == "all good"


# --- 3. spill directory stays bounded ----------------------------------------

def test_spill_dir_is_pruned(test_db, monkeypatch, tmp_path):
    d = tmp_path / "spill"
    monkeypatch.setattr(rec, "_SPILL_DIR", str(d))
    monkeypatch.setattr(rec, "SPILL_KEEP", 5)
    for i in range(12):
        rec.spill(f"payload {i}", "bash")
        time.sleep(0.002)          # keep mtimes distinct
    assert len(os.listdir(d)) <= 5

    # The newest must survive — pruning keeps the most recent, not arbitrary ones.
    newest = max((os.path.join(d, f) for f in os.listdir(d)), key=os.path.getmtime)
    assert "payload 11" in open(newest).read()
