"""Unit tests for agent/reflection.py — nightly consolidation and skill refinement."""
import json
import time
import types
import pytest

from agent import reflection, longterm


@pytest.fixture(autouse=True)
def reflection_db(test_db):
    """Init all tables that reflection._gather() queries."""
    from agent import goals
    goals.init_db()
    return test_db


# ── Mock Claude client ─────────────────────────────────────────────────────

def _make_client(monkeypatch, json_payload=None, text_payload=None):
    """Monkeypatch telemetry.create to return a controlled response."""
    from agent import telemetry

    raw = text_payload or (json.dumps(json_payload) if json_payload is not None else "[]")

    class _Block:
        type = "text"
        text = raw

    class _Usage:
        input_tokens = 100; output_tokens = 50
        cache_read_input_tokens = 0; cache_creation_input_tokens = 0

    class _Resp:
        content = [_Block()]
        usage = _Usage()
        stop_reason = "end_turn"

    monkeypatch.setattr(telemetry, "create", lambda *a, **kw: _Resp())
    return object()  # opaque client handle


# ── _gather ────────────────────────────────────────────────────────────────

class TestGather:
    def test_returns_expected_keys(self):
        data = reflection._gather(hours=24)
        assert set(data.keys()) >= {"sessions", "memories", "goals", "progress", "tasks", "entities", "awareness_events"}

    def test_empty_db_returns_empty_lists(self):
        data = reflection._gather(hours=24)
        assert data["sessions"] == []
        assert data["memories"] == []


# ── _build_digest ─────────────────────────────────────────────────────────

class TestBuildDigest:
    def test_empty_data_returns_no_activity(self):
        data = {"sessions": [], "memories": [], "goals": [], "progress": [],
                "tasks": [], "entities": [], "awareness_events": []}
        assert reflection._build_digest(data) == "(no recent activity)"

    def test_sessions_appear_in_digest(self):
        data = {"sessions": [{"id": 1, "started_at": 0, "ended_at": 0, "summary": "Did a thing"}],
                "memories": [], "goals": [], "progress": [],
                "tasks": [], "entities": [], "awareness_events": []}
        digest = reflection._build_digest(data)
        assert "Did a thing" in digest

    def test_memories_appear_in_digest(self):
        data = {"sessions": [],
                "memories": [{"id": 1, "kind": "fact", "content": "User likes Python", "importance": 7}],
                "goals": [], "progress": [], "tasks": [], "entities": [], "awareness_events": []}
        digest = reflection._build_digest(data)
        assert "User likes Python" in digest

    def test_awareness_events_capped_at_50_in_digest(self):
        events = [{"source": "screen", "content": f"event {i}"} for i in range(100)]
        data = {"sessions": [], "memories": [], "goals": [], "progress": [],
                "tasks": [], "entities": [], "awareness_events": events}
        digest = reflection._build_digest(data)
        # Only the last 50 events appear
        assert "event 99" in digest
        assert "event 0" not in digest


# ── list_reflections ───────────────────────────────────────────────────────

class TestListReflections:
    def _insert(self, status="pending", kind="pattern", confidence=0.7, action=None):
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO reflections (ts, kind, content, confidence, status, action_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), kind, "test content", confidence, status, json.dumps(action or {})),
            )
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_empty(self):
        assert reflection.list_reflections() == []

    def test_filters_by_status(self):
        self._insert("pending")
        self._insert("applied")
        rows = reflection.list_reflections(status="pending")
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"

    def test_returns_all_fields(self):
        self._insert(action={"type": "remember", "content": "hello"})
        r = reflection.list_reflections()[0]
        assert "id" in r and "kind" in r and "content" in r
        assert "confidence" in r and "action" in r
        assert r["action"]["type"] == "remember"

    def test_limit(self):
        for _ in range(5):
            self._insert()
        assert len(reflection.list_reflections(limit=3)) == 3


# ── apply_reflection ──────────────────────────────────────────────────────

class TestApplyReflection:
    def _insert_pending(self, action=None):
        with longterm._conn() as c:
            c.execute(
                "INSERT INTO reflections (ts, kind, content, confidence, status, action_json) "
                "VALUES (?, 'pattern', 'test', 0.9, 'pending', ?)",
                (time.time(), json.dumps(action or {})),
            )
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_not_found(self):
        result = reflection.apply_reflection(999)
        assert "No reflection" in result

    def test_reject_marks_rejected(self):
        rid = self._insert_pending()
        result = reflection.apply_reflection(rid, accept=False)
        assert "Rejected" in result
        rows = reflection.list_reflections(status="rejected")
        assert any(r["id"] == rid for r in rows)

    def test_already_applied_returns_message(self):
        rid = self._insert_pending()
        reflection.apply_reflection(rid, accept=True)
        result = reflection.apply_reflection(rid, accept=True)
        assert "already" in result

    def test_accept_remember_action_calls_longterm(self):
        action = {"type": "remember", "content": "Unit tests are important", "kind": "fact", "importance": 7}
        rid = self._insert_pending(action=action)
        result = reflection.apply_reflection(rid, accept=True)
        assert "Applied" in result
        memories = longterm.recall("unit tests")
        assert any("Unit tests" in m["content"] for m in memories)

    def test_accept_forget_action_removes_memory(self):
        # Create a memory to forget
        longterm.remember("Temporary fact", kind="fact", importance=3)
        with longterm._conn() as c:
            mid = c.execute("SELECT id FROM memories ORDER BY id DESC LIMIT 1").fetchone()[0]
        action = {"type": "forget", "memory_id": mid}
        rid = self._insert_pending(action=action)
        reflection.apply_reflection(rid, accept=True)
        # Memory should be gone
        mems = longterm.recall()
        assert not any(m["id"] == mid for m in mems)

    def test_accept_sets_status_to_applied(self):
        rid = self._insert_pending(action={})
        reflection.apply_reflection(rid, accept=True)
        with longterm._conn() as c:
            status = c.execute("SELECT status FROM reflections WHERE id = ?", (rid,)).fetchone()[0]
        assert status == "applied"


# ── consolidate (mocked) ──────────────────────────────────────────────────

class TestConsolidate:
    def test_empty_response_returns_zero_created(self, monkeypatch):
        client = _make_client(monkeypatch, json_payload=[])
        result = reflection.consolidate(client, hours=1)
        assert result["created"] == 0
        assert result["applied"] == 0

    def test_invalid_json_returns_error(self, monkeypatch):
        client = _make_client(monkeypatch, text_payload="not json at all")
        result = reflection.consolidate(client, hours=1)
        assert "error" in result

    def test_low_confidence_stays_pending(self, monkeypatch):
        items = [{"kind": "pattern", "content": "test", "confidence": 0.3,
                  "action": {"type": "remember", "content": "x", "kind": "fact", "importance": 5}}]
        client = _make_client(monkeypatch, json_payload=items)
        result = reflection.consolidate(client, hours=1, autosave=True)
        assert result["created"] == 1
        assert result["applied"] == 0
        rows = reflection.list_reflections(status="pending")
        assert len(rows) == 1

    def test_high_confidence_auto_applied(self, monkeypatch):
        items = [{"kind": "insight", "content": "auto-apply me", "confidence": 0.95,
                  "action": {"type": "remember", "content": "Auto fact", "kind": "fact", "importance": 6}}]
        client = _make_client(monkeypatch, json_payload=items)
        result = reflection.consolidate(client, hours=1, autosave=True)
        assert result["applied"] == 1
        rows = reflection.list_reflections(status="applied")
        assert len(rows) == 1

    def test_multiple_items_mixed_confidence(self, monkeypatch):
        items = [
            {"kind": "pattern", "content": "high", "confidence": 0.9,
             "action": {"type": "remember", "content": "high conf", "kind": "fact", "importance": 5}},
            {"kind": "insight", "content": "low",  "confidence": 0.4,
             "action": {"type": "remember", "content": "low conf",  "kind": "note", "importance": 3}},
        ]
        client = _make_client(monkeypatch, json_payload=items)
        result = reflection.consolidate(client, hours=1, autosave=True)
        assert result["created"] == 2
        assert result["applied"] == 1
        assert result["pending"] == 1

    def test_autosave_false_nothing_applied(self, monkeypatch):
        items = [{"kind": "pattern", "content": "skip", "confidence": 0.99,
                  "action": {"type": "remember", "content": "skip me", "kind": "fact", "importance": 5}}]
        client = _make_client(monkeypatch, json_payload=items)
        result = reflection.consolidate(client, hours=1, autosave=False)
        assert result["applied"] == 0

    def test_result_includes_rollback_key(self, monkeypatch):
        client = _make_client(monkeypatch, json_payload=[])
        result = reflection.consolidate(client, hours=1)
        assert "rollback" in result


# ── refine_skills (mocked) ────────────────────────────────────────────────

class TestRefineSkills:
    def test_no_failing_skills_returns_zero(self, monkeypatch, tmp_path):
        from agent import skills
        monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
        saved = dict(skills._registry); skills._registry.clear()
        client = _make_client(monkeypatch)
        result = reflection.refine_skills(client, hours=1)
        assert result == {"candidates": 0, "refined": 0, "staged": 0}
        skills._registry.update(saved)

    def test_failing_skill_triggers_rewrite(self, monkeypatch, tmp_path):
        from agent import skills
        monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
        saved = dict(skills._registry); skills._registry.clear()

        # Create a skill and log 3 failures
        skills.create_skill("flaky", "breaks", "def run(inputs): return 'ok'")
        cutoff = time.time() - 3600
        with longterm._conn() as c:
            for _ in range(3):
                c.execute(
                    "INSERT INTO skill_usage (ts, name, success, duration, error) VALUES (?, 'flaky', 0, 0.1, 'oops')",
                    (time.time(),),
                )

        # Return valid code from the "Claude" mock
        new_code = json.dumps({"code": "def run(inputs): return 'fixed'"})
        client = _make_client(monkeypatch, text_payload=new_code)
        result = reflection.refine_skills(client, hours=1)
        assert result["candidates"] == 1
        assert result["staged"] == 1   # gated: installs only on approval

        skills._registry.clear()
        skills._registry.update(saved)
