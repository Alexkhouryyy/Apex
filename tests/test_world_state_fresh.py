"""The 'current world state' line in every prompt must be current.

It says what the user is working on right now. Built every 5 minutes while
Apex runs; after Apex has been off it would otherwise be last week's guess,
presented to the model as the present.
"""
import time

import pytest

from agent import goals, longterm, world_model


@pytest.fixture(autouse=True)
def _tables(test_db):
    goals.init_db()
    world_model.init_db()


def _set(value, age):
    with longterm._conn() as c:
        c.execute("INSERT OR REPLACE INTO world_state (key, value, updated_at) VALUES (?, ?, ?)",
                  ("current", value, time.time() - age))


def test_recent_state_reaches_the_prompt(test_db):
    _set("Alex is building the car-engine motion study.", 120)
    assert "car-engine motion study" in goals.active_goals_for_prompt()


def test_stale_state_is_left_out(test_db):
    _set("Alex is debugging the relay.", world_model.FRESH_FOR + 60)
    assert "debugging the relay" not in goals.active_goals_for_prompt()
    assert world_model.get() == "Alex is debugging the relay."     # the dashboard still shows the last one


def test_lessons_reach_the_prompt(test_db, monkeypatch):
    from agent import lessons
    monkeypatch.setattr(lessons, "active", lambda limit=lessons.MAX_IN_PROMPT: [
        {"key": "k", "text": "web_browse on PDFs fails; use read_file after download", "n": 10, "rate": 0.6}])
    assert "web_browse on PDFs fails" in goals.active_goals_for_prompt()
