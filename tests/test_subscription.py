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


# ── Wiring the conversation onto the subscription ────────────────────────────

class TestTranscriptPrompt:
    """The SDK's streamed prompt accepts only `{"type": "user", ...}` messages —
    verified against the installed package. There is no way to hand it a prior
    ASSISTANT turn, so a conversation cannot be replayed as native turns.

    The alternative was `resume=<session_id>`, letting Claude Code's session
    store own the history. That is a divergence bug by construction: Apex's
    Memory would still be doing summarization and long-term-memory injection, so
    two histories would exist, and the first fallback to the API — the whole
    point of having a fallback — would make them disagree about what was said.
    """

    def test_both_roles_survive_the_crossing(self):
        from agent import subscription as sub
        out = sub.transcript_prompt(
            [{"role": "user", "content": "my name is Alex"},
             {"role": "assistant", "content": "Noted."}], "what is my name?")
        assert "Alex" in out and "Noted." in out

    def test_tool_traffic_is_summarized_not_replayed(self):
        """Replaying full tool results would blow the prompt up with output the
        next reply does not need."""
        from agent import subscription as sub
        out = sub.transcript_prompt([{"role": "assistant", "content": [
            {"type": "text", "text": "saving that"},
            {"type": "tool_use", "name": "remember", "input": {"content": "x"}},
        ]}], "ok")
        assert "remember" in out and "input" not in out

    def test_it_says_which_message_to_answer(self):
        """Handed a transcript with no framing, a model will sometimes answer
        the first message in it."""
        from agent import subscription as sub
        assert "LAST user message" in sub.transcript_prompt(
            [{"role": "user", "content": "hi"}], "and now?")

    def test_trimming_drops_the_OLDEST_turns(self):
        """The newest turns are what the next reply depends on. Keeping the
        opening and dropping the tail would be exactly backwards."""
        from agent import subscription as sub
        msgs = [{"role": "user", "content": f"message number {i} " + "x" * 400}
                for i in range(60)]
        out = sub.transcript_prompt(msgs, "latest", max_chars=3000)
        assert "message number 59" in out
        assert "message number 0 " not in out

    def test_a_trimmed_transcript_admits_it(self):
        """Silently truncating history is how a model confidently contradicts
        something it was told earlier."""
        from agent import subscription as sub
        msgs = [{"role": "user", "content": "x" * 500} for _ in range(40)]
        assert "trimmed" in sub.transcript_prompt(msgs, "?", max_chars=2000)

    def test_an_empty_history_is_just_the_question(self):
        from agent import subscription as sub
        assert sub.transcript_prompt([], "hello") == "hello"

    @pytest.mark.parametrize("junk", [None, ["not a dict"], [{}], [{"role": "system"}]])
    def test_garbage_history_does_not_raise(self, junk):
        """This runs on the conversation path; a raise would end the turn."""
        from agent import subscription as sub
        sub.transcript_prompt(junk, "hello")


class TestShouldUse:
    def test_off_by_default(self, monkeypatch):
        import config
        from agent import subscription as sub
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        ok, why = sub.should_use("agent.core/main")
        assert ok is False and "SUBSCRIPTION_ENABLED" in why

    def test_a_call_site_outside_the_list_is_refused_by_name(self, monkeypatch):
        """The measurement said background work is 3x DEARER here than Haiku.
        Nothing should reach the subscription just because it is enabled."""
        import config
        from agent import subscription as sub
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "SUBSCRIPTION_CALL_SITES", ["agent.core/main"],
                            raising=False)
        ok, why = sub.should_use("deepresearch/extract")
        assert ok is False and "deepresearch/extract" in why

    def test_it_says_why_rather_than_just_no(self, monkeypatch):
        """"The subscription did not get used" has several causes with
        different fixes."""
        import config
        from agent import subscription as sub
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        assert sub.should_use("agent.core/main")[1]


class TestConversationFallback:
    """Any failure must land on the API with the conversation intact. An
    exhausted five-hour window cannot be allowed to stop Apex working."""

    def _core(self):
        from agent.core import AgentCore
        return AgentCore.__new__(AgentCore)

    class _Mem:
        def __init__(self): self.added = []
        def get_messages(self): return [{"role": "user", "content": "hi"}]
        def add_assistant(self, content): self.added.append(content)

    def test_disabled_returns_none_so_the_api_path_runs(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        core, mem = self._core(), self._Mem()
        assert core._try_subscription("hi", mem) is None
        assert mem.added == [], "a skipped turn must not touch memory"

    def test_a_raising_sdk_falls_back(self, monkeypatch):
        import config
        from agent import subscription as sub
        monkeypatch.setattr(sub, "should_use", lambda _s: (True, ""))
        monkeypatch.setattr(sub, "run_turn", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("rate limit reached")))
        core, mem = self._core(), self._Mem()
        monkeypatch.setattr(type(core), "_effective_system_prompt",
                            lambda self: "sys", raising=False)
        monkeypatch.setattr(type(core), "_model", "claude-opus-5", raising=False)
        assert core._try_subscription("hi", mem) is None
        assert mem.added == []

    def test_an_empty_reply_falls_back_rather_than_returning_nothing(self, monkeypatch):
        """A blank turn that 'succeeded' would show the user an empty response
        and never try the API — the fail-open shape."""
        import config
        from agent import subscription as sub
        monkeypatch.setattr(sub, "should_use", lambda _s: (True, ""))
        monkeypatch.setattr(sub, "run_turn",
                            lambda *a, **k: {"text": "   ", "is_error": False})
        core, mem = self._core(), self._Mem()
        monkeypatch.setattr(type(core), "_effective_system_prompt",
                            lambda self: "sys", raising=False)
        monkeypatch.setattr(type(core), "_model", "claude-opus-5", raising=False)
        assert core._try_subscription("hi", mem) is None

    def test_an_errored_turn_falls_back(self, monkeypatch):
        from agent import subscription as sub
        monkeypatch.setattr(sub, "should_use", lambda _s: (True, ""))
        monkeypatch.setattr(sub, "run_turn",
                            lambda *a, **k: {"text": "partial", "is_error": True})
        core, mem = self._core(), self._Mem()
        monkeypatch.setattr(type(core), "_effective_system_prompt",
                            lambda self: "sys", raising=False)
        monkeypatch.setattr(type(core), "_model", "claude-opus-5", raising=False)
        assert core._try_subscription("hi", mem) is None

    def test_a_good_turn_is_recorded_in_apex_memory(self, monkeypatch):
        """THE property that keeps the two paths from drifting: whichever ran,
        Apex's Memory holds the conversation, so a later fallback sees it."""
        from agent import subscription as sub
        monkeypatch.setattr(sub, "should_use", lambda _s: (True, ""))
        monkeypatch.setattr(sub, "run_turn", lambda *a, **k: {
            "text": "done", "is_error": False, "would_have_cost_usd": 0.11})
        core, mem = self._core(), self._Mem()
        monkeypatch.setattr(type(core), "_effective_system_prompt",
                            lambda self: "sys", raising=False)
        monkeypatch.setattr(type(core), "_model", "claude-opus-5", raising=False)
        assert core._try_subscription("hi", mem) == "done"
        assert mem.added == [[{"type": "text", "text": "done"}]]

    def test_run_actually_calls_it(self):
        """The method existing is not the same as run() using it."""
        import inspect
        from agent.core import AgentCore
        assert "_try_subscription" in inspect.getsource(AgentCore.run)
