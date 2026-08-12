"""Tests for best-of-n wiring (AgentCore._rerank_eligible / _rerank_answer).

_rerank_eligible is a spend gate: every False here is money not spent. These
tests exist mostly to prove it refuses in all the cases where extra generation
would be wasted or wrong.
"""
import types

import pytest

import config


class _Core:
    """Bind the real methods to a bare object — avoids constructing AgentCore
    (which needs an API key and loads the whole tool registry)."""
    def __init__(self):
        from agent.core import AgentCore
        self._rerank_eligible = types.MethodType(AgentCore._rerank_eligible, self)
        self._rerank_answer = types.MethodType(AgentCore._rerank_answer, self)
        self.client = object()


@pytest.fixture
def core():
    return _Core()


@pytest.fixture(autouse=True)
def enabled_and_warm(monkeypatch):
    monkeypatch.setattr(config, "RERANK_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RERANK_N", 2, raising=False)
    from agent import reranker
    monkeypatch.setattr(reranker, "is_learned", lambda: True)
    yield


# --- the spend gate ----------------------------------------------------------

def test_eligible_when_enabled_warm_final_and_not_streaming(core):
    assert core._rerank_eligible("end_turn", streaming=False) is True


def test_disabled_by_config_spends_nothing(core, monkeypatch):
    monkeypatch.setattr(config, "RERANK_ENABLED", False, raising=False)
    assert core._rerank_eligible("end_turn", streaming=False) is False


def test_cold_reranker_spends_nothing(core, monkeypatch):
    """The important cost guard: don't pay n x for a reranker that would just
    return the first candidate."""
    from agent import reranker
    monkeypatch.setattr(reranker, "is_learned", lambda: False)
    assert core._rerank_eligible("end_turn", streaming=False) is False


def test_tool_use_turns_are_never_reranked(core):
    """A tool call is not an answer; reranking it would break the tool loop."""
    assert core._rerank_eligible("tool_use", streaming=False) is False


def test_streaming_is_never_reranked(core):
    """You cannot rerank text already streamed to the user."""
    assert core._rerank_eligible("end_turn", streaming=True) is False


def test_eligible_survives_a_broken_reranker(core, monkeypatch):
    from agent import reranker
    monkeypatch.setattr(reranker, "is_learned",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert core._rerank_eligible("end_turn", streaming=False) is False


# --- candidate generation ----------------------------------------------------

def _resp(text, stop_reason="end_turn"):
    blk = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(content=[blk], stop_reason=stop_reason)


def test_picks_the_reranker_winner(core, monkeypatch):
    from agent import core as core_mod, reranker
    monkeypatch.setattr(core_mod.telemetry, "create", lambda *a, **k: _resp("second answer"))
    monkeypatch.setattr(reranker, "rerank", lambda cands: {
        "chosen_index": 1, "scores": [0.1, 0.9], "reordered": True, "learned": True,
        "chosen": cands[1]})
    monkeypatch.setattr(reranker, "record", lambda *a, **k: None)
    content, text = core._rerank_answer({"model": "m"}, ["first_content"], "first answer")
    assert text == "second answer"


def test_falls_back_to_first_when_extra_generation_fails(core, monkeypatch):
    from agent import core as core_mod
    monkeypatch.setattr(core_mod.telemetry, "create",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")))
    content, text = core._rerank_answer({"model": "m"}, ["orig"], "original answer")
    assert text == "original answer" and content == ["orig"]


def test_tool_use_candidates_are_discarded(core, monkeypatch):
    """An extra candidate that came back as a tool call is not a valid answer."""
    from agent import core as core_mod
    monkeypatch.setattr(core_mod.telemetry, "create",
                        lambda *a, **k: _resp("tool call", stop_reason="tool_use"))
    content, text = core._rerank_answer({"model": "m"}, ["orig"], "original answer")
    assert text == "original answer"   # only 1 valid candidate -> unchanged


def test_no_temperature_override_when_thinking_enabled(core, monkeypatch):
    """Anthropic requires temperature=1 with thinking; we must not set it."""
    seen = {}
    from agent import core as core_mod, reranker
    def spy(client, *, call_site, **kwargs):
        seen.update(kwargs)
        return _resp("x")
    monkeypatch.setattr(core_mod.telemetry, "create", spy)
    monkeypatch.setattr(reranker, "rerank", lambda c: {"chosen_index": 0, "scores": [0, 0],
                                                       "reordered": False, "learned": True,
                                                       "chosen": c[0]})
    monkeypatch.setattr(reranker, "record", lambda *a, **k: None)
    core._rerank_answer({"model": "m", "thinking": {"type": "enabled"}}, ["c"], "a")
    assert "temperature" not in seen


def test_out_of_range_index_falls_back(core, monkeypatch):
    from agent import core as core_mod, reranker
    monkeypatch.setattr(core_mod.telemetry, "create", lambda *a, **k: _resp("second"))
    monkeypatch.setattr(reranker, "rerank", lambda c: {"chosen_index": 99, "scores": [0, 0],
                                                       "reordered": True, "learned": True,
                                                       "chosen": c[0]})
    monkeypatch.setattr(reranker, "record", lambda *a, **k: None)
    content, text = core._rerank_answer({"model": "m"}, ["orig"], "original")
    assert text == "original"
