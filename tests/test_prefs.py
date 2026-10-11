"""Preference learning must read the real turn-log schema, including missing turns."""
import json
import time
from types import SimpleNamespace

import pytest

from agent import feedback, longterm, prefs, world_model


@pytest.fixture
def preference_db(test_db, monkeypatch):
    feedback.init_db()
    world_model.init_db()
    prompts = []

    def create(client, **kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(content=[SimpleNamespace(text="Keep answers concise.")])

    monkeypatch.setattr(prefs.telemetry, "create", create)
    return prompts


def log(role, content, turn=1, session=1):
    with longterm._conn() as conn:
        conn.execute("INSERT INTO turn_log (ts, session_id, turn_index, role, content_json) "
                     "VALUES (?, ?, ?, ?, ?)", (time.time(), session, turn, role, json.dumps(content)))


def test_distillation_reads_role_json_and_counts_feedback_once(preference_db):
    log("user", {"text": "Explain my error"})
    log("assistant", {"text": "Let me investigate"})
    log("tool_result", {"text": "TOOL OUTPUT MUST NOT BECOME THE ANSWER"})
    log("assistant", [{"type": "text", "text": "Here is the fix"},
                      {"type": "tool_use", "text": "TOOL CALL MUST NOT BECOME TEXT"}])
    log("assistant", {"text": "ANOTHER SESSION"}, session=2)
    feedback.record(1, session_id=1, turn_index=1, comment="Clear and concise")
    log("user", {"text": "Show the details"}, turn=2)
    log("assistant", {"text": "Too much background"}, turn=2)
    feedback.record(-1, session_id=1, turn_index=2, comment="Stay focused")

    assert prefs.distill(object(), force=True) == "Keep answers concise."
    prompt, = preference_db
    assert "POSITIVE FEEDBACK (thumbs up, 1 examples)" in prompt
    assert "NEGATIVE FEEDBACK (thumbs down, 1 examples)" in prompt
    for text in ("Explain my error", "Here is the fix", "Clear and concise", "Stay focused"):
        assert text in prompt
    for text in ("Let me investigate", "TOOL OUTPUT", "TOOL CALL", "ANOTHER SESSION"):
        assert text not in prompt
    assert prefs.get() == "Keep answers concise."


def test_feedback_without_a_turn_still_uses_the_owners_comment(preference_db):
    feedback.record(1, session_id=99, turn_index=99, comment="Prefer short answers")
    assert prefs.distill(object(), force=True) == "Keep answers concise."
    assert "Prefer short answers" in preference_db[0]


def test_malformed_turn_does_not_drop_feedback_or_crash(preference_db):
    log("user", {"text": "broken"})
    with longterm._conn() as conn:
        conn.execute("UPDATE turn_log SET content_json = 'invalid json'")
    feedback.record(-1, session_id=1, turn_index=1, comment="Please explain the error")
    assert prefs.distill(object(), force=True) == "Keep answers concise."
    assert "Please explain the error" in preference_db[0]
