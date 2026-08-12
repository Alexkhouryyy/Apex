"""Integrity tests for the learning loop's data path.

These pin three bugs that made the reward model untrustworthy or the scoreboard
unable to produce a verdict. Each test names the failure it prevents.
"""
import json
import time

import pytest

from agent import reranker as rr, longterm, feedback


@pytest.fixture(autouse=True)
def clear_index():
    rr._index_cache.update({"ts": 0.0, "liked": [], "disliked": []})
    rr._warned_no_session = False
    yield
    rr._index_cache.update({"ts": 0.0, "liked": [], "disliked": []})


def _assistant_row(session_id, turn_index, text):
    """One assistant row. Several may share a turn_index — that is the bug."""
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO turn_log (ts, session_id, turn_index, role, content_json, tool_calls_json) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), session_id, turn_index, "assistant", json.dumps({"text": text}), "[]"),
        )


def _rate(session_id, turn_index, rating):
    with longterm._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO turn_feedback (ts, session_id, turn_index, rating, comment, source) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), session_id, turn_index, rating, "", "test"),
        )


# --- BUG 1: reward-model corruption from tool-loop fan-out -------------------

def test_tool_heavy_turn_yields_exactly_one_sample(test_db):
    """log_turn('assistant') runs INSIDE the tool loop, so one rated turn can
    write several assistant rows. Naively joining replicated the rating across
    all of them — training the reward model on partial preambles."""
    rr.init_db(); feedback.init_db()
    # A turn with 3 tool rounds: 3 intermediate rows, then the real answer.
    _assistant_row(1, 5, "")                      # empty intermediate
    _assistant_row(1, 5, "Let me check that...")  # partial preamble
    _assistant_row(1, 5, "Still working...")      # partial preamble
    _assistant_row(1, 5, "THE FINAL ANSWER")      # what the user actually rated
    _rate(1, 5, +1)

    liked, disliked = rr._rated_responses()
    assert len(liked) == 1, f"one rating must yield one sample, got {liked}"
    assert liked[0] == "THE FINAL ANSWER"          # the last row, not a preamble
    assert disliked == []


def test_limit_applies_to_ratings_not_joined_rows(test_db):
    """Before the fix, LIMIT counted joined rows, so a few tool-heavy turns
    could crowd out every other rated sample."""
    rr.init_db(); feedback.init_db()
    for turn in range(3):
        for i in range(5):                        # 5 rows per turn
            _assistant_row(1, turn, f"answer {turn} part {i}")
        _rate(1, turn, +1)
    liked, disliked = rr._rated_responses()
    assert len(liked) == 3                        # 3 ratings -> 3 samples, not 15


def test_distinct_turns_are_all_kept(test_db):
    rr.init_db(); feedback.init_db()
    _assistant_row(1, 0, "good one"); _rate(1, 0, +1)
    _assistant_row(1, 1, "bad one");  _rate(1, 1, -1)
    liked, disliked = rr._rated_responses()
    assert liked == ["good one"] and disliked == ["bad one"]


# --- BUG 2: silent join death + double counting ------------------------------

def test_record_refuses_rows_that_could_never_join(test_db, capsys):
    """A NULL session_id can never join to turn_feedback, so stats() would
    silently report rated=0 forever. Refuse the write and say so."""
    rr.init_db()
    rr.record({"scores": [0, 1], "chosen_index": 1, "reordered": True,
               "learned": True, "chosen": {}}, session_id=None, turn_index=3)
    with longterm._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM rerank_events").fetchone()[0] == 0
    assert "not recorded" in capsys.readouterr().out


def test_duplicate_event_does_not_double_count(test_db):
    """UNIQUE(session_id, turn_index) + upsert, mirroring turn_feedback."""
    rr.init_db(); feedback.init_db()
    ev = {"scores": [0, 1], "chosen_index": 1, "reordered": True,
          "learned": True, "chosen": {}}
    rr.record(ev, session_id=1, turn_index=7)
    rr.record(ev, session_id=1, turn_index=7)      # same turn again
    with longterm._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM rerank_events").fetchone()[0] == 1

    _assistant_row(1, 7, "answer"); _rate(1, 7, +1)
    s = rr.stats()
    assert s["events"] == 1 and s["rated"] == 1    # one rating counted once


def test_stats_can_finally_produce_a_verdict(test_db):
    """End-to-end: a rerank event on a turn that later gets rated must yield a
    non-null approval figure — the thing the Learning tab could never show."""
    rr.init_db(); feedback.init_db()
    rr.record({"scores": [0, 1], "chosen_index": 1, "reordered": True,
               "learned": True, "chosen": {}}, session_id=1, turn_index=2)
    _assistant_row(1, 2, "the reordered answer")
    _rate(1, 2, +1)
    s = rr.stats()
    assert s["approval_when_reordered"] == 100
