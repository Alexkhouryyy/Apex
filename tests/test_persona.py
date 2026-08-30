"""The persona is the first thing the model reads, every turn.

`agent/persona.py` is 36 lines, on by default, and prepended to the system
prompt in `agent/core.py:_effective_system_prompt()` ahead of SYSTEM_PROMPT —
deliberately, so its character rules outrank the base tone. No test referenced
it, which made it an UNPROVEN row in docs/APEX_GAP_ANALYSIS.md.

Small and load-bearing is the worst combination to leave untested: a prefix that
silently stopped being prepended would change how every reply sounds, and
nothing would fail.
"""
from __future__ import annotations

import pytest

import config
from agent import core, persona


@pytest.fixture
def agent(monkeypatch, test_db):
    """`test_db` (conftest.py) points longterm.DB_PATH at a fresh temp file,
    but only initializes longterm's OWN tables — every other module (26 of
    them) owns its own schema and its own init_db(). _effective_system_prompt()
    pulls active goals via agent/goals.py, so that one has to be created here
    too, or this depends on the real production DB already having a `goals`
    table from unrelated prior use — which is exactly why this passed locally
    and failed on a clean CI checkout with no such file at all.
    """
    from agent import goals as _goals_mod
    _goals_mod.init_db()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    return core.AgentCore()


def _blocks_text(agent) -> list[str]:
    return [b.get("text", "") for b in agent._effective_system_prompt()]


# ── the flag ──────────────────────────────────────────────────────────────────

def test_enabled_by_default(monkeypatch):
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", True, raising=False)
    prefix = persona.get_persona_prefix()
    assert prefix and "JARVIS" in prefix


def test_disabled_returns_nothing(monkeypatch):
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", False, raising=False)
    assert persona.get_persona_prefix() is None


# ── it has to actually reach the prompt ───────────────────────────────────────

def test_persona_reaches_the_system_prompt(agent, monkeypatch):
    """The regression a silent break would cause: every reply changes character
    and nothing errors."""
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", True, raising=False)
    joined = "\n".join(_blocks_text(agent))
    assert "JARVIS" in joined, "the persona never reached the prompt"


def test_persona_is_the_first_block(agent, monkeypatch):
    """Its whole purpose is priority over the base prompt's tone. Behind
    SYSTEM_PROMPT it is just a suggestion."""
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", True, raising=False)
    blocks = _blocks_text(agent)
    assert "JARVIS" in blocks[0], (
        f"persona is not first — it sits at index "
        f"{next((i for i, b in enumerate(blocks) if 'JARVIS' in b), None)}"
    )


def test_persona_precedes_the_base_system_prompt(agent, monkeypatch):
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", True, raising=False)
    blocks = _blocks_text(agent)
    persona_at = next(i for i, b in enumerate(blocks) if "JARVIS" in b)
    base_at = next(i for i, b in enumerate(blocks) if core.SYSTEM_PROMPT[:60] in b)
    assert persona_at < base_at


def test_no_persona_leaks_when_disabled(agent, monkeypatch):
    """Turning it off must remove the character rules, not merely stop
    announcing them."""
    monkeypatch.setattr(config, "JARVIS_PERSONA_ENABLED", False, raising=False)
    joined = "\n".join(_blocks_text(agent))
    assert "JARVIS" not in joined
    assert 'Address the user as "sir"' not in joined


def test_a_broken_persona_does_not_break_the_turn(agent, monkeypatch):
    """core wraps the import in try/except. That fail-open is correct here — a
    personality fault must not cost you the conversation — but it means a broken
    persona is invisible, so pin that the turn survives."""
    monkeypatch.setattr(persona, "get_persona_prefix",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    blocks = _blocks_text(agent)
    assert any(core.SYSTEM_PROMPT[:60] in b for b in blocks), \
        "a failing persona took the whole system prompt with it"


# ── --no-proactive, which had stopped meaning anything ────────────────────────

def test_awareness_honours_the_proactive_flag(monkeypatch):
    """`--no-proactive` sets config.PROACTIVE_ENABLED = False, main.py prints
    "Proactive: off", and the dashboard reports it.

    Nothing acted on it. The only reader was ProactiveMonitor.start(), and that
    monitor ran only when AWARENESS_ENABLED was False — not the default. So
    under a normal config the flag printed "off" and Apex carried on speaking
    up. Deleting proactive.py is what made this visible; it predates the
    deletion.
    """
    import inspect
    from agent import awareness

    src = inspect.getsource(awareness.AwarenessMonitor)
    assert "PROACTIVE_ENABLED" in src, (
        "the awareness review loop does not consult PROACTIVE_ENABLED, so "
        "--no-proactive is decoration again"
    )
    # ...and it must gate the speaking, not the watching: the cortex, Guardian
    # and Time Capsule are awareness, not interruption.
    review = src.split("def _review_loop", 1)[1]
    gate_at = review.index("PROACTIVE_ENABLED")
    speak_at = review.index("self.proactive_check")
    assert gate_at < speak_at, "the flag is checked after the interruption already happened"
