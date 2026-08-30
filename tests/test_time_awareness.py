"""Apex must know what day it is.

Nothing in the system prompt carried a clock and there was no tool to ask, while
the scheduler fired dated jobs, the Time Capsule surfaced callbacks "days or
weeks later", goals ran on day/week/month/quarter horizons, and lessons expired
after 30 days. All of that was reasoned about by a model whose only sense of
"now" was its training data.

This is a different failure class from the nine "built but never ran" bugs, and
a nastier one. A missing clock does not raise. The model answers with a
confident, plausible, wrong date; nothing logs and nothing fails. No audit that
looks for crashes or unreachable code can find it — only asking Apex a question
and reading the answer.

The other half of the fix is where the block sits: after the cache_control
breakpoint. A per-turn timestamp placed before it would invalidate the cached
prefix on every request and quietly multiply the bill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import config
from agent import core


@pytest.fixture
def agent(monkeypatch, test_db):
    """See tests/test_persona.py's `agent` fixture for why goals.init_db() is
    needed here specifically — test_db only creates longterm's own tables."""
    from agent import goals as _goals_mod
    _goals_mod.init_db()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    return core.AgentCore()


# --- the block itself --------------------------------------------------------

def test_block_states_the_actual_date():
    now = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
    text = core.time_block(now)
    assert "2026" in text
    assert "August" in text
    assert "18" in text
    assert "Tuesday" in text, "weekday matters for 'next Monday' style requests"
    assert "14:30" in text


def test_block_is_portable_to_windows():
    """`%-d` is glibc-only and raises ValueError on Windows, which is where this
    actually runs."""
    text = core.time_block(datetime(2026, 8, 5, 9, 5, tzinfo=timezone.utc))
    assert "5 August" in text, f"day rendered oddly: {text!r}"


def test_block_tells_the_model_not_to_guess():
    """Stating the date is not enough — a model with a strong prior about 'now'
    needs to be told this one wins."""
    text = core.time_block(datetime(2026, 8, 18, tzinfo=timezone.utc)).lower()
    assert "never guess" in text
    assert "authoritative" in text
    assert "relative" in text, "relative dates must be anchored to it"


def test_block_carries_a_timezone():
    tz = timezone(timedelta(hours=3))
    text = core.time_block(datetime(2026, 8, 18, 14, 30, tzinfo=tz))
    assert "+0300" in text


def test_block_is_generated_fresh():
    a = core.time_block(datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc))
    b = core.time_block(datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc))
    assert a != b, "the clock is baked in rather than read per call"


# --- it has to actually reach the model --------------------------------------

def test_system_prompt_contains_the_date(agent):
    """The regression. A time_block nobody calls is the tenth 'built but never
    ran'."""
    blocks = agent._effective_system_prompt()
    joined = "\n".join(b.get("text", "") for b in blocks)
    assert "CURRENT DATE AND TIME" in joined, "the prompt still has no clock"
    assert str(datetime.now().year) in joined


def test_date_block_is_after_the_cache_breakpoint(agent):
    """Placed before it, a per-turn timestamp invalidates the cached prefix on
    every single request — a silent, permanent cost increase."""
    blocks = agent._effective_system_prompt()
    last_cached = max(
        (i for i, b in enumerate(blocks) if b.get("cache_control")), default=-1)
    time_idx = next(i for i, b in enumerate(blocks)
                    if "CURRENT DATE AND TIME" in b.get("text", ""))
    assert time_idx > last_cached, (
        f"time block at {time_idx} is at or before the cache breakpoint at "
        f"{last_cached} — every turn would bust the prompt cache"
    )


def test_the_time_block_carries_no_cache_control(agent):
    blocks = agent._effective_system_prompt()
    tb = next(b for b in blocks if "CURRENT DATE AND TIME" in b.get("text", ""))
    assert not tb.get("cache_control"), "caching a per-turn timestamp freezes it"


# --- the tool ----------------------------------------------------------------

def test_current_time_tool_is_offered(agent):
    assert any(t["name"] == "current_time" for t in agent._all_tools())


def test_current_time_tool_returns_a_real_time(agent):
    out = core._execute_tool("current_time", {})
    assert str(datetime.now().year) in out


def test_current_time_tool_honours_a_timezone(agent):
    out = core._execute_tool("current_time", {"timezone": "Asia/Tokyo"})
    assert "+0900" in out or "JST" in out, out


def test_bad_timezone_is_an_answer_not_a_crash(agent):
    out = core._execute_tool("current_time", {"timezone": "Mars/Olympus_Mons"})
    assert "Unknown timezone" in out
