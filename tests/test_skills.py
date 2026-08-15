"""Unit tests for the self-improving skills system.

Covers agent/skills.py (registry, usage telemetry, non-destructive overwrite)
and agent/reflection.py:refine_skills (the nightly skill-repair pass).
"""
import json
import pytest

from agent import skills


@pytest.fixture
def skill_dir(tmp_path, monkeypatch):
    """Isolate skill files in a temp dir and reset the in-memory registry."""
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path)
    saved = dict(skills._registry)
    skills._registry.clear()
    yield tmp_path
    skills._registry.clear()
    skills._registry.update(saved)


_GOOD = "def run(inputs):\n    return 'ok'"
_FAILS = "def run(inputs):\n    raise ValueError('boom')"


class TestCreateSkill:
    def test_happy_path(self, skill_dir):
        result = skills.create_skill("greet", "says hi", _GOOD)
        assert "created and loaded" in result
        assert (skill_dir / "greet.py").exists()
        assert "greet" in skills._registry

    def test_syntax_error_rejected(self, skill_dir):
        result = skills.create_skill("bad", "broken", "def run(inputs:\n    return 1")
        assert "Syntax error" in result
        assert not (skill_dir / "bad.py").exists()

    def test_missing_run_rejected(self, skill_dir):
        result = skills.create_skill("norun", "no entrypoint", "x = 1")
        assert "must define" in result
        assert not (skill_dir / "norun.py").exists()

    def test_invalid_identifier_rejected(self, skill_dir):
        result = skills.create_skill("not-valid", "bad name", _GOOD)
        assert "Invalid skill name" in result


class TestNonDestructiveOverwrite:
    def test_failed_overwrite_keeps_previous_version(self, skill_dir, monkeypatch):
        """A failed reload during overwrite must restore the working skill.

        The post-write reload is forced to fail once (simulating a bad rewrite
        from refine_skills); create_skill must roll the file + registry back.
        """
        skills.create_skill("calc", "v1", "def run(inputs):\n    return 'v1'")
        good_source = (skill_dir / "calc.py").read_text()

        real_load = skills._load
        state = {"first": True}

        def flaky_load(name):
            if state["first"]:
                state["first"] = False
                raise RuntimeError("simulated bad rewrite")
            return real_load(name)

        monkeypatch.setattr(skills, "_load", flaky_load)
        result = skills.create_skill("calc", "v2 attempt", "def run(inputs):\n    return 'v2'")

        assert "kept at previous version" in result
        assert (skill_dir / "calc.py").read_text() == good_source
        assert skills._registry["calc"]["run"]({}) == "v1"

    def test_failed_create_of_new_skill_removes_file(self, skill_dir, monkeypatch):
        """When there is no previous version, a failed reload removes the file."""
        def boom(name):
            raise RuntimeError("import failed")

        monkeypatch.setattr(skills, "_load", boom)
        result = skills.create_skill("fresh", "new", _GOOD)
        assert "import failed" in result
        assert not (skill_dir / "fresh.py").exists()


class TestUsageTelemetry:
    def test_successful_run_logs_success_row(self, skill_dir, test_db):
        from agent import longterm
        skills.create_skill("ok", "works", _GOOD)
        assert skills.run_skill("ok", {}) == "ok"
        with longterm._conn() as c:
            row = c.execute(
                "SELECT name, success, duration, error FROM skill_usage WHERE name = 'ok'"
            ).fetchone()
        assert row is not None
        assert row[1] == 1
        assert row[2] is not None and row[2] >= 0
        assert row[3] is None

    def test_failing_run_logs_failure_row(self, skill_dir, test_db):
        from agent import longterm
        skills.create_skill("boom", "fails", _FAILS)
        out = skills.run_skill("boom", {})
        assert "error" in out.lower()
        with longterm._conn() as c:
            row = c.execute(
                "SELECT success, error FROM skill_usage WHERE name = 'boom'"
            ).fetchone()
        assert row[0] == 0
        assert "boom" in row[1]


class TestFailureStats:
    def test_only_skills_over_threshold_returned(self, skill_dir, test_db):
        skills.create_skill("flaky", "fails", _FAILS)
        skills.create_skill("rare", "fails", _FAILS)
        for _ in range(3):
            skills.run_skill("flaky", {})
        skills.run_skill("rare", {})

        stats = skills.failure_stats(hours=24, min_failures=3)
        names = {s["name"] for s in stats}
        assert "flaky" in names
        assert "rare" not in names

        flaky = next(s for s in stats if s["name"] == "flaky")
        assert flaky["failures"] == 3
        assert any("boom" in e for e in flaky["errors"])


class TestSkillHelpers:
    def test_read_source(self, skill_dir):
        skills.create_skill("src", "has source", _GOOD)
        assert "def run" in skills.read_source("src")
        assert skills.read_source("missing") is None

    def test_get_description(self, skill_dir):
        skills.create_skill("desc", "my description", _GOOD)
        assert skills.get_description("desc") == "my description"
        assert "unknown" in skills.get_description("unknown")


class TestRefineSkills:
    def test_failing_skill_is_rewritten(self, skill_dir, test_db, monkeypatch):
        from agent import reflection, telemetry

        skills.create_skill("repairme", "needs fixing", _FAILS)
        for _ in range(3):
            skills.run_skill("repairme", {})

        payload = json.dumps({"code": "def run(inputs):\n    return 'fixed'"})

        class FakeResp:
            content = [type("B", (), {"text": payload, "type": "text"})()]

        monkeypatch.setattr(telemetry, "create", lambda *a, **k: FakeResp())

        result = reflection.refine_skills(client=None, hours=24)
        assert result["staged"] == 1   # gated: installs only on approval

        # The old code keeps running until a human approves the rewrite — a
        # model editing the code an always-on agent executes should not take
        # effect unattended.
        from agent import approvals
        assert skills.run_skill("repairme", {}) != "fixed"

        pending = [p for p in approvals.list_pending() if p["kind"] == "skill_code"]
        assert len(pending) == 1
        approvals.approve(pending[0]["id"])
        assert skills.run_skill("repairme", {}) == "fixed"

    def test_no_failures_means_nothing_refined(self, skill_dir, test_db, monkeypatch):
        from agent import reflection, telemetry

        def should_not_be_called(*a, **k):
            raise AssertionError("telemetry.create called with no failing skills")

        monkeypatch.setattr(telemetry, "create", should_not_be_called)
        result = reflection.refine_skills(client=None, hours=24)
        assert result["candidates"] == 0
        assert result["refined"] == 0
