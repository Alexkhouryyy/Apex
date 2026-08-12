"""Resident-mode feedback capture.

Resident mode is the only surface where best-of-n reranking actually fires, and
until now it recorded no feedback at all — so the reward model could never learn
from voice usage and the scoreboard could never score it.

The subtle requirement: telemetry._turn_index is a process-wide global that the
scheduler, cortex, channels and dashboard all advance concurrently. Reading it at
feedback time would rate whatever background turn happened last. The turn index
must be CAPTURED when the spoken answer completes.
"""
import json
import time

import pytest

from agent import feedback, telemetry, longterm


@pytest.fixture(autouse=True)
def fresh_turn_counter():
    telemetry.set_session(1)
    yield


def _simulate(user_text, last_voice_turn, session_id=1):
    """Exercises the REAL function that app/resident.py calls, not a copy of it,
    so this test cannot drift away from the shipped behaviour."""
    return feedback.capture_phrase(
        user_text, session_id=session_id, last_turn=last_voice_turn, source="voice"
    ) is not None


def test_positive_phrase_rates_the_previous_answer(test_db):
    feedback.init_db()
    assert _simulate("that was perfect", last_voice_turn=4) is True
    row = feedback.for_turn(1, 4)
    assert row["rating"] == 1 and row["source"] == "voice"


def test_negative_phrase_is_captured(test_db):
    feedback.init_db()
    assert _simulate("no that's wrong", last_voice_turn=2) is True
    assert feedback.for_turn(1, 2)["rating"] == -1


def test_ordinary_speech_is_not_treated_as_feedback(test_db):
    """A normal request must fall through and run as a turn, not be swallowed."""
    feedback.init_db()
    assert _simulate("what's on my calendar tomorrow", last_voice_turn=3) is False
    assert feedback.for_turn(1, 3) is None


def test_nothing_is_rated_before_the_first_answer(test_db):
    feedback.init_db()
    assert _simulate("that was perfect", last_voice_turn=0) is False


def test_captured_turn_survives_a_background_turn(test_db):
    """THE reason for capturing rather than reading live: the cortex/scheduler
    advance the global counter between the answer and the user's reaction."""
    feedback.init_db()
    telemetry.set_session(1)
    telemetry.bump_turn()                       # the user's spoken turn -> 1
    captured = telemetry.current_turn()
    assert captured == 1

    telemetry.bump_turn(); telemetry.bump_turn()  # background cortex/scheduler turns
    assert telemetry.current_turn() == 3          # live value has drifted

    _simulate("that was perfect", last_voice_turn=captured)
    assert feedback.for_turn(1, 1)["rating"] == 1   # rated the SPOKEN turn
    assert feedback.for_turn(1, 3) is None          # not the background one


def test_rating_can_be_corrected(test_db):
    """turn_feedback is an upsert, so 'actually that was wrong' flips it."""
    feedback.init_db()
    _simulate("that was perfect", last_voice_turn=5)
    assert feedback.for_turn(1, 5)["rating"] == 1
    _simulate("actually that was wrong", last_voice_turn=5)
    assert feedback.for_turn(1, 5)["rating"] == -1


def test_voice_is_a_valid_source_and_not_coerced(test_db):
    """An invalid source silently degrades to 'api' and pollutes by_source."""
    feedback.init_db()
    _simulate("that was perfect", last_voice_turn=1)
    assert feedback.for_turn(1, 1)["source"] == "voice"


def test_capture_is_safe_with_no_session(test_db):
    """Channels have no session in scope; capture must decline, not crash."""
    feedback.init_db()
    assert feedback.capture_phrase("that was perfect", session_id=None,
                                   last_turn=3, source="voice") is None


def test_capture_never_raises(test_db, monkeypatch):
    """Feedback capture must never break a conversation."""
    feedback.init_db()
    monkeypatch.setattr(feedback, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert feedback.capture_phrase("that was perfect", session_id=1,
                                   last_turn=2) is None


def test_captured_feedback_reaches_the_reward_model(test_db):
    """End-to-end: a voice rating must become a training sample."""
    from agent import reranker as rr
    rr._index_cache.update({"ts": 0.0, "liked": [], "disliked": []})
    feedback.init_db(); rr.init_db()
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO turn_log (ts, session_id, turn_index, role, content_json, tool_calls_json) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), 1, 6, "assistant", json.dumps({"text": "the spoken answer"}), "[]"),
        )
    _simulate("that was perfect", last_voice_turn=6)
    liked, _ = rr._rated_responses()
    assert "the spoken answer" in liked
