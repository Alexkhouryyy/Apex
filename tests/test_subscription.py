"""Running Apex on a subscription, with its safety gates intact.

`agent/subscription.py` routes a turn through the Claude Agent SDK, which
authenticates via the `claude` CLI and so draws on a Pro/Max usage window
instead of API credits.

These tests mock the SDK deliberately: a real call spends the user's window, and
a test suite that quietly consumes the plan it is testing would be its own kind
of bug. The live behaviour was verified by hand and the numbers are recorded in
the module docstring and the commit.

The test that matters is `test_a_refused_tool_does_not_run`. The first
implementation gated with `can_use_tool`, and the SDK warned that an
`allowed_tools` entry auto-approves before the callback is consulted — so the
gate never fired, a refusal was ignored, and the tool ran anyway. Apex's approval
machinery present, wired, and bypassed: the same shape as the other seventeen
findings, caught only because the SDK said so out loud.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent import subscription as sub

SRC = Path(sub.__file__).read_text()


# ── availability reports which half is missing ────────────────────────────────

def test_available_names_the_missing_sdk(monkeypatch):
    import builtins
    real = builtins.__import__

    def _no_sdk(name, *a, **k):
        if name == "claude_agent_sdk":
            raise ImportError("nope")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _no_sdk)

    ok, why = sub.available()
    assert ok is False
    assert "claude-agent-sdk" in why


def test_available_names_the_missing_cli(monkeypatch):
    monkeypatch.setattr(sub.shutil, "which", lambda _: None)
    ok, why = sub.available()
    assert ok is False
    assert "claude" in why and "PATH" in why


def test_run_turn_refuses_when_unavailable(monkeypatch):
    monkeypatch.setattr(sub, "available", lambda: (False, "no cli"))
    with pytest.raises(RuntimeError, match="unavailable"):
        sub.run_turn("sys", "hi", [], lambda n, a: "x")


# ── the safety gate ───────────────────────────────────────────────────────────

def _hook_input(name, args=None):
    return {"tool_name": name, "tool_input": args or {}}


def _decision(result):
    return ((result or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def test_a_refused_tool_does_not_run():
    """The regression. Apex says no; the SDK must be told no."""
    import asyncio
    hook = sub._permission_hook(lambda name, args: False)
    out = asyncio.run(hook(_hook_input("mcp__apex__remember"), None, None))
    assert _decision(out) == "deny"
    assert "safety policy" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_an_allowed_tool_proceeds():
    import asyncio
    hook = sub._permission_hook(lambda name, args: True)
    assert _decision(asyncio.run(hook(_hook_input("mcp__apex__remember"), None, None))) is None


def test_the_gate_sees_apex_names_not_mcp_names():
    """agent/safety.py knows `run_shell`, not `mcp__apex__run_shell`. Passing the
    prefixed name through would silently match no policy at all."""
    import asyncio
    seen = []
    hook = sub._permission_hook(lambda name, args: seen.append(name) or True)
    asyncio.run(hook(_hook_input("mcp__apex__run_shell", {"cmd": "ls"}), None, None))
    assert seen == ["run_shell"]


def test_harness_tools_are_not_apex_business():
    """Claude Code's own tools (ToolSearch and friends) belong to the harness.
    Judging them would deny things Apex has no policy about."""
    import asyncio
    seen = []
    hook = sub._permission_hook(lambda name, args: seen.append(name) or False)
    out = asyncio.run(hook(_hook_input("ToolSearch", {"query": "x"}), None, None))
    assert _decision(out) is None
    assert seen == []


def test_a_raising_safety_check_denies_rather_than_allows():
    """Fail closed. An exception in the gate must not become permission."""
    import asyncio

    def _boom(name, args):
        raise RuntimeError("policy engine down")
    out = asyncio.run(sub._permission_hook(_boom)(_hook_input("mcp__apex__x"), None, None))
    assert _decision(out) == "deny"
    assert "failed" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_gate_is_a_pretooluse_hook_not_can_use_tool():
    """Structural, and it earned its place. `can_use_tool` is shadowed by any
    `allowed_tools` entry that permits a whole tool — which is exactly how Apex
    exposes all 85 of its tools — so the callback never runs and every refusal
    is ignored."""
    assert "PreToolUse" in SRC, "the permission gate is not a PreToolUse hook"
    live = [ln for ln in SRC.splitlines()
            if "can_use_tool=" in ln and not ln.strip().startswith("#")]
    assert not live, f"can_use_tool is wired again and will be shadowed: {live}"


# ── tool bridging ─────────────────────────────────────────────────────────────

def test_apex_tool_schemas_bridge_unchanged():
    """Apex's input_schema is full JSON Schema and the SDK takes it directly.
    Rewriting it would be a second schema to keep in sync."""
    import config
    calls = []

    class _FakeSdk:
        @staticmethod
        def tool(name, desc, schema):
            calls.append((name, schema))
            return lambda fn: fn

    import sys
    sys.modules["claude_agent_sdk"] = _FakeSdk
    try:
        schema = {"type": "object", "properties": {"content": {"type": "string"}},
                  "required": ["content"]}
        sub._bridge([{"name": "remember", "description": "d", "input_schema": schema}],
                    lambda n, a: "ok")
    finally:
        del sys.modules["claude_agent_sdk"]

    assert calls[0][0] == "remember"
    assert calls[0][1] is schema, "the schema was rewritten instead of passed through"


# ── the arithmetic that shaped the routing ────────────────────────────────────

def test_window_cost_is_reported_per_call():
    """The obvious plan — send cheap high-volume work here — is wrong, and the
    reason is this number rather than an opinion."""
    one = sub.window_cost(1)
    many = sub.window_cost(1000)
    assert one["harness_tokens"] == sub.HARNESS_TOKENS
    assert many["harness_tokens"] == 1000 * sub.HARNESS_TOKENS
    assert "shared" in many["note"]


def test_the_measured_harness_size_is_recorded():
    """~26.5k tokens per call, measured, not guessed. If someone lowers this to
    make the routing look better, the docstring evidence disagrees."""
    assert 20_000 < sub.HARNESS_TOKENS < 35_000
    assert "cache_read=23525" in SRC or "26.5k" in SRC


def test_settings_sources_are_pinned_empty():
    """Without setting_sources=[] the SDK inherits whatever CLAUDE.md sits in the
    working directory, and Apex's persona is silently replaced by a file."""
    assert re.search(r"setting_sources\s*=\s*\[\]", SRC)
