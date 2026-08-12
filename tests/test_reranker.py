"""Tests for the learning loop (agent/reranker.py).

The important assertions are about what it REFUSES to do: it must not invent a
preference when it has no data, and it must be honest about whether it learned.
"""
import json
import time

import pytest

from agent import reranker as rr, longterm, feedback


@pytest.fixture(autouse=True)
def clear_index():
    rr._index_cache.update({"ts": 0.0, "liked": [], "disliked": []})
    yield
    rr._index_cache.update({"ts": 0.0, "liked": [], "disliked": []})


def _rate(session_id: int, turn_index: int, text: str, rating: int):
    """Write an assistant turn plus its rating — the join the reward depends on."""
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO turn_log (ts, session_id, turn_index, role, content_json, tool_calls_json) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), session_id, turn_index, "assistant", json.dumps({"text": text}), "[]"),
        )
        c.execute(
            "INSERT OR REPLACE INTO turn_feedback (ts, session_id, turn_index, rating, comment, source) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), session_id, turn_index, rating, "", "test"),
        )


# --- cold start: refuse to invent a preference -------------------------------

def test_cold_start_keeps_order_and_admits_it(test_db):
    rr.init_db(); feedback.init_db()
    res = rr.rerank([{"text": "answer A"}, {"text": "answer B"}])
    assert res["chosen_index"] == 0        # original order preserved
    assert res["learned"] is False         # and it says so
    assert res["reordered"] is False


def test_is_learned_false_below_threshold(test_db):
    rr.init_db(); feedback.init_db()
    assert rr.is_learned() is False
    _rate(1, 0, "a good answer", +1)
    assert rr.is_learned() is False        # 1 sample is not enough


def test_score_is_zero_and_unlearned_without_history(test_db):
    rr.init_db(); feedback.init_db()
    score, learned = rr.score_text("anything")
    assert score == 0.0 and learned is False


# --- the join that makes the reward possible ---------------------------------

def test_rated_responses_joins_rating_to_text(test_db):
    rr.init_db(); feedback.init_db()
    _rate(1, 0, "liked response text", +1)
    _rate(1, 1, "disliked response text", -1)
    liked, disliked = rr._rated_responses()
    assert "liked response text" in liked
    assert "disliked response text" in disliked


def test_unrated_turns_are_ignored(test_db):
    rr.init_db(); feedback.init_db()
    with longterm._conn() as c:  # an assistant turn with NO rating
        c.execute(
            "INSERT INTO turn_log (ts, session_id, turn_index, role, content_json, tool_calls_json) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), 9, 0, "assistant", json.dumps({"text": "unrated"}), "[]"),
        )
    liked, disliked = rr._rated_responses()
    assert "unrated" not in liked and "unrated" not in disliked


# --- learned behaviour (embeddings stubbed so the test is deterministic) -----

def _stub_embeddings(monkeypatch):
    """Map text -> a deterministic unit vector so cosine is predictable."""
    import numpy as np
    def fake_embed(text: str):
        t = (text or "").lower()
        # 2-D: axis 0 = "concise", axis 1 = "rambling"
        v = np.array([1.0, 0.0] if "concise" in t else
                     [0.0, 1.0] if "rambling" in t else
                     [0.7071, 0.7071], dtype=np.float32)
        return v.tobytes()
    monkeypatch.setattr(longterm, "_embed", fake_embed)


def test_prefers_candidate_resembling_liked_answers(test_db, monkeypatch):
    rr.init_db(); feedback.init_db()
    _stub_embeddings(monkeypatch)
    _rate(1, 0, "concise answer one", +1)
    _rate(1, 1, "concise answer two", +1)
    _rate(1, 2, "rambling answer three", -1)

    res = rr.rerank([{"text": "a rambling reply"}, {"text": "a concise reply"}])
    assert res["learned"] is True
    assert res["chosen"]["text"] == "a concise reply"
    assert res["reordered"] is True          # it genuinely moved off index 0


def test_scores_are_ordered_sensibly(test_db, monkeypatch):
    rr.init_db(); feedback.init_db()
    _stub_embeddings(monkeypatch)
    _rate(1, 0, "concise a", +1); _rate(1, 1, "concise b", +1); _rate(1, 2, "rambling c", -1)
    good, _ = rr.score_text("concise text")
    bad, _ = rr.score_text("rambling text")
    assert good > bad


# --- edge cases --------------------------------------------------------------

def test_empty_and_single_candidate(test_db):
    rr.init_db(); feedback.init_db()
    assert rr.rerank([])["chosen_index"] == -1
    one = rr.rerank([{"text": "only"}])
    assert one["chosen_index"] == 0 and one["reordered"] is False


def test_model_prior_is_zero_for_unknown_model(test_db):
    assert rr.model_prior("") == 0.0
    assert rr.model_prior("never-compared-model") == 0.0


# --- measurement -------------------------------------------------------------

def test_record_and_stats(test_db):
    rr.init_db(); feedback.init_db()
    rr.record({"scores": [0.1, 0.2], "chosen_index": 1, "reordered": True,
               "learned": True, "chosen": {"model": "m"}}, session_id=1, turn_index=0)
    s = rr.stats()
    assert s["events"] == 1 and s["reordered"] == 1


def test_stats_compares_reordered_vs_not(test_db):
    """The honest scoreboard: approval on reordered turns vs untouched ones."""
    rr.init_db(); feedback.init_db()
    rr.record({"scores": [0, 1], "chosen_index": 1, "reordered": True,
               "learned": True, "chosen": {}}, session_id=1, turn_index=0)
    _rate(1, 0, "reordered answer", +1)
    rr.record({"scores": [0, 1], "chosen_index": 0, "reordered": False,
               "learned": True, "chosen": {}}, session_id=1, turn_index=1)
    _rate(1, 1, "untouched answer", -1)
    s = rr.stats()
    assert s["approval_when_reordered"] == 100
    assert s["approval_when_not_reordered"] == 0


def test_record_never_raises(test_db, monkeypatch):
    monkeypatch.setattr(longterm, "_conn",
                        lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
    rr.record({"scores": [], "chosen_index": 0})   # must not raise
