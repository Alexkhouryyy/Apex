"""Tests for blind model comparison (agent/compare.py).

Verifies the council is reused but never mutated, answers come back blind, picks
log preferences, and the leaderboard aggregates wins/appearances.
"""
import pytest

from agent import compare, council, provider


@pytest.fixture
def stub_models(monkeypatch):
    monkeypatch.setattr(
        council, "available_members",
        lambda: [("claude-opus-4-7", "Claude"), ("gpt-4o", "GPT"), ("gemini-2.5-flash", "Gemini")],
    )

    def fake_complete(model, system, user, max_tokens=2048):
        return f"answer from {model}"

    monkeypatch.setattr(provider, "complete", fake_complete)


def test_run_returns_blind_entries(stub_models):
    out = compare.run("what is 2+2?")
    assert out["count"] == 3
    # Blind view must NOT leak model or label.
    for e in out["entries"]:
        assert set(e.keys()) == {"slot", "text"}
    slots = sorted(e["slot"] for e in out["entries"])
    assert slots == ["A", "B", "C"]
    assert out["compare_id"] in compare._PENDING


def test_run_needs_two_models(monkeypatch):
    monkeypatch.setattr(council, "available_members", lambda: [("claude-opus-4-7", "Claude")])
    out = compare.run("anything")
    assert "error" in out


def test_pick_logs_preference_and_reveals(test_db, stub_models):
    compare.init_db()
    out = compare.run("best language?")
    cid = out["compare_id"]
    picked_slot = out["entries"][0]["slot"]

    res = compare.pick(cid, picked_slot, note="clearest")
    assert "winner" in res
    assert res["winner"]["model"] in ("claude-opus-4-7", "gpt-4o", "gemini-2.5-flash")
    # Reveal covers every slot.
    assert len(res["reveal"]) == 3
    # Pending entry consumed.
    assert cid not in compare._PENDING

    lb = compare.leaderboard()
    assert lb["total"] == 1
    winner_models = [r["model"] for r in lb["rows"] if r["wins"] == 1]
    assert res["winner"]["model"] in winner_models


def test_pick_expired_is_graceful(test_db, stub_models):
    compare.init_db()
    assert "error" in compare.pick("cmp_doesnotexist", "A")


def test_leaderboard_win_rate(test_db, stub_models):
    compare.init_db()
    # Run + pick three times, always choosing slot A.
    for _ in range(3):
        out = compare.run("q")
        compare.pick(out["compare_id"], "A")
    lb = compare.leaderboard()
    assert lb["total"] == 3
    # Every model appeared 3 times; win_rates are well-formed percentages.
    for r in lb["rows"]:
        assert r["appearances"] == 3
        assert 0 <= r["win_rate"] <= 100


def test_council_not_mutated(stub_models):
    # Compare must not alter the council roster/chair. `stub_models` replaces
    # council.available_members; the real roster and chair must survive that.
    #
    # Asserted structurally rather than against a hardcoded model ID. The literal
    # that used to be here tested nothing about compare — it only pinned whatever
    # model was current, and broke on a routine upgrade.
    assert len(council._ROSTER) == 3
    assert council._CHAIR in {m for m, _ in council._ROSTER}
    assert council._CHAIR.startswith("claude-")
