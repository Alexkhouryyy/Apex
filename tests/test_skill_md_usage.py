"""Regression tests for markdown-skill creation vs the curator.

The bug these pin: skill_md.manage('create') wrote the SKILL.md but never seeded
the .usage.json sidecar. curator computes `age_days = 999.0` when a skill has no
`last_used_at` (curator.py:176-177), and 999 >= ARCHIVE_DAYS (90) — so EVERY
freshly authored skill was archived on the curator's very next run and silently
disappeared from the system prompt before it was ever used.
"""
import json

import pytest

from agent import skill_md


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    """Point the skill store at a temp dir so tests never touch ~/.apex."""
    d = tmp_path / "skills"
    monkeypatch.setattr(skill_md, "_SKILLS_DIR", d)
    monkeypatch.setattr(skill_md, "_USAGE_FILE", d / ".usage.json")
    return d


def _create(name="demo-skill"):
    return skill_md.manage("create", name=name, description="A demo skill.",
                           content="# Demo\n\nBody here.", _bypass_approval=True)


def test_create_seeds_usage_sidecar(skills_dir):
    """The fix: creation must record last_used_at, or the curator eats it."""
    _create()
    usage = json.loads((skills_dir / ".usage.json").read_text())
    assert "demo-skill" in usage
    assert usage["demo-skill"]["last_used_at"] > 0


def test_created_skill_survives_the_curator(skills_dir, monkeypatch):
    """End-to-end: a brand-new skill must not be archived on the next run."""
    from agent import curator
    monkeypatch.setattr(curator, "_SKILLS_MD_DIR", skills_dir)
    monkeypatch.setattr(curator, "_USAGE_FILE", skills_dir / ".usage.json", raising=False)
    monkeypatch.setattr(curator, "_load_usage",
                        lambda: json.loads((skills_dir / ".usage.json").read_text()))
    _create()
    report = str(curator.run(dry_run=True))
    assert "ARCHIVE: demo-skill" not in report
    assert "demo-skill" in [s["name"] for s in skill_md.list_skills()]


def test_skill_is_discoverable_after_create(skills_dir):
    _create()
    found = skill_md.list_skills()
    assert [s["name"] for s in found] == ["demo-skill"]
    assert found[0]["description"] == "A demo skill."


def test_view_returns_full_body_and_bumps_usage(skills_dir):
    _create()
    before = json.loads((skills_dir / ".usage.json").read_text())["demo-skill"]["use_count"]
    body = skill_md.manage("view", name="demo-skill")
    after = json.loads((skills_dir / ".usage.json").read_text())["demo-skill"]["use_count"]
    assert "Body here." in body
    assert after == before + 1


def test_create_is_idempotent_guarded(skills_dir):
    _create()
    assert "already exists" in _create()


def test_install_bundled_is_idempotent_and_self_healing(skills_dir):
    """Bundled skills ship in the repo so they survive a machine rebuild;
    ~/.apex/skills is runtime state. Installing must be safe to run every boot."""
    first = skill_md.install_bundled()
    assert first >= 1                      # clone-app ships with Apex
    assert skill_md.install_bundled() == 0  # second boot installs nothing
    names = [s["name"] for s in skill_md.list_skills()]
    assert "clone-app" in names
    # Deleting it and rebooting restores it.
    import shutil
    shutil.rmtree(skills_dir / "clone-app")
    assert skill_md.install_bundled() == 1


def test_bundled_install_seeds_usage(skills_dir):
    """Same curator trap as create: a bundled skill with no last_used_at would be
    archived on the first curator run."""
    skill_md.install_bundled()
    usage = json.loads((skills_dir / ".usage.json").read_text())
    assert usage["clone-app"]["last_used_at"] > 0


def test_description_with_colons_survives_frontmatter_parse(skills_dir):
    """clone-app's description contains quoted phrases and punctuation; the
    naive `partition(':')` parser must still recover the whole value."""
    desc = 'Clone anything: use when the user says "make our own X", or "clone X".'
    skill_md.manage("create", name="colon-skill", description=desc,
                    content="# X", _bypass_approval=True)
    got = [s for s in skill_md.list_skills() if s["name"] == "colon-skill"][0]
    assert got["description"] == desc
