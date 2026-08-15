"""New built-but-not-working code must fail the suite.

Nine times a feature here has been fully written, plausibly tested, and simply
not running. Every one was found by accident, which is the actual finding: Apex
fails silently by design, so "never wired up" and "working perfectly" are
indistinguishable from the outside.

The one-time cleanup is the least valuable half of the fix. This is the durable
half — the audit runs on every test run, and anything new fails here. Same move
as the webhook startup audit: the repair that lasts is the one that reports
state, not the one that tidies up once.

Every allowlist entry carries a written reason, and there is a test enforcing
that, because an allowlist without reasons becomes a place findings go to die.
"""
import pytest

from tools import wiring_audit

# Known, deliberate exceptions. A reason is mandatory — see
# test_every_allowlist_entry_has_a_reason.
ALLOWLIST = {
    "config.KB_INDEX_PATHS is defined but never read anywhere":
        "Dead knob: the KB indexes paths passed per-call instead. Harmless, but "
        "a setting that does nothing is a small lie — slated for removal.",
    "config.OCR_CONFIDENCE_THRESHOLD is defined but never read anywhere":
        "Dead knob left from the OCR path; the vision tools do not consult it.",
    "config.SCREEN_HOTKEY is defined but never read anywhere":
        "Dead knob; resident mode uses RESIDENT_GLOBAL_HOTKEY instead.",
    "agent/longterm.py: table 'memory_audit' is created and written but never "
    "read back":
        "Reserved audit table, never populated or queried in code. Kept because "
        "dropping a table is a migration; flagged for removal.",
}


def _findings() -> list[str]:
    out = []
    for name, items in wiring_audit.run().items():
        out.extend(items)
    return out


def test_no_new_orphaned_wiring():
    """The whole point. A feature that is built but not reachable fails here."""
    unexpected = [f for f in _findings() if f not in ALLOWLIST]
    assert unexpected == [], (
        "New built-but-not-working code detected. Either wire it up, or add it "
        "to ALLOWLIST in this file with a reason:\n  " + "\n  ".join(unexpected))


def test_every_allowlist_entry_has_a_reason():
    """An allowlist without reasons is a place findings go to die."""
    for finding, reason in ALLOWLIST.items():
        assert reason and len(reason) > 30, f"no real reason given for: {finding}"


def test_the_allowlist_does_not_rot():
    """An entry that no longer matches a real finding is stale and misleading —
    it implies a known problem that has in fact been fixed."""
    current = set(_findings())
    stale = [f for f in ALLOWLIST if f not in current]
    assert stale == [], f"ALLOWLIST entries no longer found; remove them: {stale}"


# --- the detectors must actually detect ---------------------------------------

def test_init_db_check_catches_an_unwired_module(tmp_path, monkeypatch):
    """A detector that cannot catch the bug that motivated it is decoration.

    Restraint shipped with init_db() uncalled and held nothing forever. This
    rebuilds that exact shape and asserts the check fires.
    """
    mod = tmp_path / "agent"
    mod.mkdir()
    (mod / "lonely.py").write_text("def init_db() -> None:\n    pass\n")
    (tmp_path / "main.py").write_text("print('nothing calls lonely')\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    assert any("lonely.py" in f for f in wiring_audit.orphan_init_db())


def test_init_db_check_accepts_lazy_self_initialisation(tmp_path, monkeypatch):
    """restraint and threads self-initialise on first use, which is wired."""
    mod = tmp_path / "agent"
    mod.mkdir()
    (mod / "lazy.py").write_text(
        "def _ensure_db() -> None:\n    init_db()\n\n"
        "def init_db() -> None:\n    pass\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    assert wiring_audit.orphan_init_db() == []


def test_init_db_check_understands_underscore_aliases(tmp_path, monkeypatch):
    """This codebase aliases as `from agent import x as _x`, and a naive \\b
    regex never matches `_x` because underscore is a word character. That false
    positive reported correctly-wired modules as broken — an audit that cries
    wolf is one people learn to skip.
    """
    mod = tmp_path / "agent"
    mod.mkdir()
    (mod / "aliased.py").write_text("def init_db() -> None:\n    pass\n")
    (tmp_path / "main.py").write_text(
        "from agent import aliased as _aliased\n_aliased.init_db()\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    assert wiring_audit.orphan_init_db() == []


def test_ws_check_catches_an_event_with_no_listener(tmp_path, monkeypatch):
    """The live_research shape: events broadcast into the void for months."""
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "noisy.py").write_text(
        'ws_manager.broadcast_threadsafe({"type": "nobody_listens", "x": 1})\n')
    static = tmp_path / "dashboard" / "static"
    static.mkdir(parents=True)
    (static / "app.js").write_text("// handles nothing\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    assert any("nobody_listens" in f for f in wiring_audit.orphan_ws_events())


def test_ws_check_ignores_tool_schema_keywords(tmp_path, monkeypatch):
    """A first pass flagged 'array', 'integer' and 'tool_use' — Anthropic schema
    keywords, not events. Scoping to real broadcast calls is what makes the
    signal usable."""
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "schema.py").write_text(
        'TOOLS = [{"input_schema": {"type": "array"}, "type": "tool_use"}]\n')
    static = tmp_path / "dashboard" / "static"
    static.mkdir(parents=True)
    (static / "app.js").write_text("// nothing\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    assert wiring_audit.orphan_ws_events() == []


def test_config_check_catches_a_phantom_limit(tmp_path, monkeypatch):
    """MAX_SUBAGENTS documented a safety cap that nothing enforced. A phantom
    limit is worse than no limit: config.py reads as a guarantee."""
    (tmp_path / "config.py").write_text("PHANTOM_LIMIT = 5\nREAL_ONE = 3\n")
    (tmp_path / "user.py").write_text("import config\nprint(config.REAL_ONE)\n")
    monkeypatch.setattr(wiring_audit, "ROOT", tmp_path)
    findings = wiring_audit.dead_config_flags()
    assert any("PHANTOM_LIMIT" in f for f in findings)
    assert not any("REAL_ONE" in f for f in findings)


def test_the_audit_never_raises(monkeypatch):
    """It runs in the test suite and ideally at boot; a crashing auditor would
    be its own instance of the problem."""
    monkeypatch.setattr(wiring_audit, "ROOT", wiring_audit.ROOT / "does_not_exist")
    for findings in wiring_audit.run().values():
        assert isinstance(findings, list)


# --- the two real defects this audit found ------------------------------------

def test_max_subagents_is_actually_enforced(monkeypatch):
    """config.MAX_SUBAGENTS documented a cap that nothing checked. A phantom
    safety limit is worse than none: anyone reading config.py concludes an
    always-on agent cannot spawn without bound. It could."""
    import config
    from agent import orchestrator

    monkeypatch.setattr(config, "MAX_SUBAGENTS", 2, raising=False)
    monkeypatch.setattr(orchestrator, "_agent_factory", lambda: None)
    monkeypatch.setattr(orchestrator, "_subagents", {
        "a": {"status": "running"}, "b": {"status": "running"}})

    out = orchestrator.spawn("researcher", "another one")
    assert "Refused" in out
    assert "MAX_SUBAGENTS" in out, "the refusal must say which limit stopped it"


def test_finished_subagents_free_up_capacity(monkeypatch):
    """The cap counts running agents, not lifetime spawns — otherwise Apex
    would permanently seize up after five tasks."""
    import config
    from agent import orchestrator

    monkeypatch.setattr(config, "MAX_SUBAGENTS", 2, raising=False)
    monkeypatch.setattr(orchestrator, "_agent_factory", lambda: None)
    monkeypatch.setattr(orchestrator, "_subagents", {
        "a": {"status": "done"}, "b": {"status": "error"}})
    assert "Refused" not in orchestrator.spawn("researcher", "fine")


def test_threads_works_without_an_explicit_init_db(test_db):
    """threads.init_db() was never called from anywhere, so threads_surfaced
    never existed. Asserts the feature FUNCTIONS on a fresh DB — checking only
    that nothing raises would have passed the whole time it was broken."""
    from agent import threads
    threads._ready = False
    assert threads._already_surfaced() == set()      # queries a real table
