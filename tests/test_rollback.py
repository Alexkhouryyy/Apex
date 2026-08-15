"""Tests for agent/rollback.py — auto-rollback of harmful skill rewrites."""
import time
import pytest

from agent import longterm, feedback, outcomes, rollback, skills


_GOOD_V1 = "def run(inputs):\n    return 'v1'"
_GOOD_V2 = "def run(inputs):\n    return 'v2'"


@pytest.fixture
def rb_db(test_db, tmp_path, monkeypatch):
    """Isolated DB + skill dir; feedback and rollback tables initialised."""
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
    saved_reg = dict(skills._registry)
    skills._registry.clear()
    feedback.init_db()
    yield tmp_path
    skills._registry.clear()
    skills._registry.update(saved_reg)


# ── helpers ────────────────────────────────────────────────────────────────

def _add_feedback(session_id, turn_index, rating):
    feedback.record(rating, session_id=session_id, turn_index=turn_index)


def _add_skill_run(name, session_id, turn_index, ts=None):
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO skill_usage (ts, name, success, duration, session_id, turn_index) "
            "VALUES (?, ?, 1, 0.1, ?, ?)",
            (ts or time.time(), name, session_id, turn_index),
        )


def _insert_rewrite(name, old_source, new_source, pre_rate=None, pre_turns=0, trigger="reflection"):
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO skill_rewrites "
            "(ts, name, old_source, new_source, trigger, pre_approval_rate, pre_rated_turns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), name, old_source, new_source, trigger, pre_rate, pre_turns),
        )
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── check_rewrites — skips ──────────────────────────────────────────────────

class TestCheckRewritesSkips:
    def test_no_rewrites_returns_zero_checked(self, rb_db):
        r = rollback.check_rewrites()
        assert r["checked"] == 0

    def test_skips_when_not_enough_post_turns(self, rb_db):
        # Create a real skill so rollback can restore if needed
        skills.create_skill("myskill", "desc", _GOOD_V1)
        rid = _insert_rewrite("myskill", "old", "new", pre_rate=0.9, pre_turns=10)

        # Only 2 rated turns after rewrite — below MIN_RATED_TURNS (5)
        for i in range(2):
            _add_skill_run("myskill", session_id=1, turn_index=i)
            _add_feedback(session_id=1, turn_index=i, rating=1)

        r = rollback.check_rewrites()
        assert r["skipped_not_enough_data"] == 1
        assert r["rolled_back"] == 0

    def test_dry_run_does_not_write(self, rb_db):
        skills.create_skill("dryone", "desc", _GOOD_V1)
        _insert_rewrite("dryone", "old", "new", pre_rate=0.9, pre_turns=10)

        for i in range(rollback.MIN_RATED_TURNS):
            _add_skill_run("dryone", session_id=1, turn_index=i)
            _add_feedback(1, i, -1)  # all bad → triggers rollback condition

        r = rollback.check_rewrites(dry_run=True)
        assert r["dry_run"] is True
        assert r["rolled_back"] == 1  # counted but not executed

        # DB status should still be 'active'
        with longterm._conn() as c:
            row = c.execute("SELECT status FROM skill_rewrites").fetchone()
        assert row[0] == "active"


# ── check_rewrites — rollback path ─────────────────────────────────────────

class TestCheckRewritesRollback:
    def test_rolls_back_when_rate_drops_below_threshold(self, rb_db):
        # create_skill auto-snapshots on overwrite, so just call it twice
        skills.create_skill("worker", "desc", _GOOD_V1)
        skills.create_skill("worker", "desc", _GOOD_V2)
        # pre_approval_rate will be None (no usage data yet) → floor 50% used

        # 5 post-rewrite runs, all thumbs-down → post_rate=0.0, delta=-0.5 < -0.2
        for i in range(rollback.MIN_RATED_TURNS):
            _add_skill_run("worker", session_id=2, turn_index=i)
            _add_feedback(2, i, -1)

        r = rollback.check_rewrites()
        assert r["rolled_back"] == 1

        # DB row should be marked rolled_back
        with longterm._conn() as c:
            row = c.execute(
                "SELECT status, rollback_reason FROM skill_rewrites WHERE name = 'worker'"
            ).fetchone()
        assert row[0] == "rolled_back"
        assert "dropped" in row[1]

        # Skill on disk should now be v1 content
        content = skills._skill_path("worker").read_text()
        assert "v1" in content

    def test_no_rollback_when_rate_holds(self, rb_db):
        skills.create_skill("stable", "desc", _GOOD_V1)
        old_src = skills._skill_path("stable").read_text()
        skills.create_skill("stable", "desc", _GOOD_V2)
        new_src = skills._skill_path("stable").read_text()
        _insert_rewrite("stable", old_src, new_src, pre_rate=0.7, pre_turns=10)

        # 5 post-rewrite runs, all thumbs-up → approval_rate=1.0, delta=+0.3
        for i in range(rollback.MIN_RATED_TURNS):
            _add_skill_run("stable", session_id=3, turn_index=i)
            _add_feedback(3, i, 1)

        r = rollback.check_rewrites()
        assert r["rolled_back"] == 0

    def test_no_old_source_skips_rollback_gracefully(self, rb_db):
        skills.create_skill("fresh", "desc", _GOOD_V1)
        # Simulate a brand-new skill (no old_source)
        _insert_rewrite("fresh", None, "new_src", pre_rate=0.9, pre_turns=5)

        for i in range(rollback.MIN_RATED_TURNS):
            _add_skill_run("fresh", session_id=4, turn_index=i)
            _add_feedback(4, i, -1)

        # Should not crash; cannot restore without old_source
        r = rollback.check_rewrites()
        assert r["rolled_back"] == 1  # counted as attempted
        # But status may or may not have changed — what matters is no exception


# ── confirm path ────────────────────────────────────────────────────────────

class TestConfirmPath:
    def test_confirms_rewrite_after_enough_good_turns(self, rb_db):
        skills.create_skill("steady", "desc", _GOOD_V1)
        skills.create_skill("steady", "desc", _GOOD_V2)
        # create_skill already inserts a rewrite snapshot via _record_rewrite

        # CONFIRM_TURNS (default 20) rated turns, all thumbs-up
        for i in range(rollback.CONFIRM_TURNS):
            _add_skill_run("steady", session_id=5, turn_index=i)
            _add_feedback(5, i, 1)

        r = rollback.check_rewrites()
        assert r["confirmed"] >= 1
        assert r["rolled_back"] == 0

        with longterm._conn() as c:
            row = c.execute(
                "SELECT status FROM skill_rewrites WHERE name = 'steady' ORDER BY ts DESC"
            ).fetchone()
        assert row[0] == "confirmed"


# ── list_rewrites ───────────────────────────────────────────────────────────

class TestListRewrites:
    def test_empty(self, rb_db):
        assert rollback.list_rewrites() == []

    def test_returns_most_recent_first(self, rb_db):
        _insert_rewrite("a", "old", "new1", pre_rate=0.8)
        time.sleep(0.01)
        _insert_rewrite("b", "old", "new2", pre_rate=0.6)
        rows = rollback.list_rewrites()
        assert rows[0]["name"] == "b"
        assert rows[1]["name"] == "a"

    def test_delta_computed(self, rb_db):
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO skill_rewrites "
                "(ts, name, old_source, new_source, trigger, "
                " pre_approval_rate, pre_rated_turns, post_approval_rate, post_rated_turns) "
                "VALUES (?, 'x', 'o', 'n', 'manual', 0.8, 10, 0.5, 8)",
                (time.time(),),
            )
        rows = rollback.list_rewrites()
        assert rows[0]["delta"] == pytest.approx(-0.3, abs=1e-3)

    def test_days_filter(self, rb_db):
        old_ts = time.time() - 40 * 86400
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO skill_rewrites (ts, name, old_source, new_source) VALUES (?, 'z', 'o', 'n')",
                (old_ts,),
            )
        assert rollback.list_rewrites(days=30) == []


# ── create_skill snapshot integration ──────────────────────────────────────

class TestCreateSkillSnapshot:
    def test_no_snapshot_on_first_create(self, rb_db):
        skills.create_skill("brand_new", "desc", _GOOD_V1)
        with longterm._conn() as c:
            count = c.execute("SELECT COUNT(*) FROM skill_rewrites").fetchone()[0]
        assert count == 0  # first-time creation: no old_source → no snapshot

    def test_snapshot_on_overwrite(self, rb_db):
        skills.create_skill("evolving", "desc", _GOOD_V1)
        skills.create_skill("evolving", "desc", _GOOD_V2)
        with longterm._conn() as c:
            row = c.execute(
                "SELECT name, trigger FROM skill_rewrites WHERE name = 'evolving'"
            ).fetchone()
        assert row is not None
        assert row[0] == "evolving"

    def test_reflection_trigger_recorded(self, rb_db):
        skills.create_skill("reflskill", "desc", _GOOD_V1)
        skills.create_skill("reflskill", "desc", _GOOD_V2, _trigger="reflection",
                            _bypass_approval=True)  # simulates post-approval install
        with longterm._conn() as c:
            row = c.execute(
                "SELECT trigger FROM skill_rewrites WHERE name = 'reflskill'"
            ).fetchone()
        assert row[0] == "reflection"
