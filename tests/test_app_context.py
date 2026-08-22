"""App-aware context re-roles the agent, on every turn, untested.

`agent/app_context.py` is called from `agent/core.py:2105` inside
_effective_system_prompt(), so whatever it returns is injected into every
request. It shifts Apex's role based on the foreground window — VSCode makes it
a code reviewer, a browser makes it a researcher.

Window detection needs an OS window manager and cannot run here. Everything
downstream of the title is pure, and that is where a wrong answer would do
damage: matching the wrong profile silently re-roles the assistant, and matching
one when detection failed would re-role it based on nothing.
"""
from __future__ import annotations

import pytest

from agent import app_context


def _with_title(monkeypatch, title):
    monkeypatch.setattr(app_context, "_get_active_window_title", lambda: title)


@pytest.mark.parametrize("title,expected", [
    ("main.py - Visual Studio Code", "Code Reviewer"),
    ("Google Chrome — Anthropic docs", "Research Assistant"),
])
def test_a_known_app_selects_its_profile(monkeypatch, title, expected):
    _with_title(monkeypatch, title)
    profile = app_context.detect_active_profile()
    assert profile is not None, f"no profile matched {title!r}"
    assert profile["name"] == expected


def test_matching_is_case_insensitive(monkeypatch):
    _with_title(monkeypatch, "MAIN.PY - VISUAL STUDIO CODE")
    assert app_context.detect_active_profile()["name"] == "Code Reviewer"


def test_an_unknown_app_matches_nothing(monkeypatch):
    """Better no role shift than a wrong one — a wrong profile changes how every
    answer that turn is written, with nothing to show it happened."""
    _with_title(monkeypatch, "Solitaire")
    assert app_context.detect_active_profile() is None


def test_no_detectable_window_yields_no_block(monkeypatch):
    _with_title(monkeypatch, None)
    assert app_context.detect_active_profile() is None
    assert app_context.get_context_block() is None


def test_the_block_names_the_role_and_tone(monkeypatch):
    _with_title(monkeypatch, "app.py - Visual Studio Code")
    block = app_context.get_context_block()
    assert block and "ACTIVE CONTEXT" in block
    assert "Code Reviewer" in block
    assert "Tone:" in block and "Focus:" in block


def test_detection_failure_returns_none_not_a_broken_string(monkeypatch):
    """It goes straight into the system prompt. A traceback string or a
    half-formatted block would be sent to the model as instructions."""
    def _boom():
        raise RuntimeError("no window manager")
    monkeypatch.setattr(app_context, "_get_active_window_title", _boom)
    assert app_context.get_context_block() is None


def test_it_can_be_switched_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "APP_CONTEXT_ENABLED", False, raising=False)
    _with_title(monkeypatch, "main.py - Visual Studio Code")
    assert app_context.get_context_block() is None


def test_every_profile_is_complete():
    """format_profile_for_prompt indexes four keys directly; a profile missing
    one raises KeyError inside the system-prompt builder."""
    for p in app_context._PROFILES:
        for key in ("matches", "name", "role", "tone", "focus"):
            assert key in p, f"profile {p.get('name', p)} has no {key!r}"
        assert p["matches"], f"profile {p['name']} matches nothing"
