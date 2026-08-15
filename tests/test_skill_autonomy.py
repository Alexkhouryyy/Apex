"""Autonomously-rewritten executable code must not install itself.

Enabling the consolidation heartbeat (step 1 of the learning work) also enabled
reflection.refine_skills(), which asks a model to rewrite failing skills and then
installed the result. Those skills are imported and executed on every later call,
with full agent privileges. Before the heartbeat, consolidate ran only when the
model chose to call reflect_now — so the path effectively never fired, and
turning it on quietly enabled unattended self-modification of running code.

Worse, create_skill validated by exec()ing the generated code in-process, so
module-level statements ran immediately — before approval, and even if the code
was then discarded.
"""
import pytest

from agent import approvals, longterm, skills


@pytest.fixture(autouse=True)
def _db(test_db, tmp_path, monkeypatch):
    approvals.init_db()
    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    yield


GOOD = "def run(inputs):\n    return 'ok'\n"


def test_validation_never_executes_the_code(tmp_path):
    """A module-level side effect must not fire during validation."""
    marker = tmp_path / "pwned.txt"
    evil = (f"import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('executed')\n"
            f"def run(inputs):\n    return 'x'\n")
    skills.create_skill("evil_skill", "d", evil, _trigger="reflection")
    assert not marker.exists(), "generated code executed during validation"


def test_an_autonomous_rewrite_is_staged_not_installed():
    out = skills.create_skill("auto_skill", "d", GOOD, _trigger="reflection")
    assert "STAGED" in out
    assert not (skills.SKILLS_DIR / "auto_skill.py").exists()
    pending = [p for p in approvals.list_pending() if p["kind"] == "skill_code"]
    assert len(pending) == 1


def test_approving_installs_it():
    skills.create_skill("auto_skill", "d", GOOD, _trigger="reflection")
    pending = [p for p in approvals.list_pending() if p["kind"] == "skill_code"][0]
    approvals.approve(pending["id"])
    assert (skills.SKILLS_DIR / "auto_skill.py").exists()


def test_rejecting_installs_nothing():
    skills.create_skill("auto_skill", "d", GOOD, _trigger="reflection")
    pending = [p for p in approvals.list_pending() if p["kind"] == "skill_code"][0]
    approvals.reject(pending["id"])
    assert not (skills.SKILLS_DIR / "auto_skill.py").exists()


def test_a_manual_skill_still_installs_directly():
    """The human-driven path is unchanged; only autonomy is gated."""
    out = skills.create_skill("manual_skill", "d", GOOD, _trigger="manual")
    assert "STAGED" not in out
    assert (skills.SKILLS_DIR / "manual_skill.py").exists()


def test_summarising_a_staged_rewrite_does_not_install_it():
    """A bug I introduced while fixing this: the apply branch was duplicated
    into _summarize, so merely rendering the approval list would have installed
    the code it was describing."""
    summary = approvals._summarize("skill_code", {
        "name": "sneaky", "code": GOOD, "trigger": "reflection"})
    assert "sneaky" in summary
    assert not (skills.SKILLS_DIR / "sneaky.py").exists()


def test_code_without_run_is_refused():
    out = skills.create_skill("bad", "d", "x = 1\n", _trigger="manual")
    assert "must define" in out


def test_syntax_errors_are_reported_not_raised():
    out = skills.create_skill("bad", "d", "def run(:\n", _trigger="manual")
    assert "Syntax error" in out
