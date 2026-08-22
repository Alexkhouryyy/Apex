"""The durable record of what Apex observed, with no test behind it.

`agent/perception.py` is initialised at boot in main.py and app/resident.py,
written by every awareness watcher (`agent/awareness.py:48`), and exposed to the
model as the `query_perception` and `recall_at_time` tools. It is how Apex
answers "what was I doing on Tuesday".

It was an UNPROVEN row in docs/APEX_GAP_ANALYSIS.md. Everything in it is plain
SQLite over FTS5, so there was never a reason for that except nobody having done
it — and a search index that silently stops matching returns an empty list, which
reads exactly like "nothing happened".
"""
from __future__ import annotations

import time

import pytest

from agent import perception


@pytest.fixture
def db(test_db):
    perception.init_db()
    return test_db


def test_an_event_survives_and_is_searchable(db):
    perception.log_event("screen", "opened the invoice spreadsheet")
    hits = perception.query("invoice")
    assert hits, "a logged event could not be found again"
    assert "invoice" in hits[0]["content"]
    assert hits[0]["source"] == "screen"


def test_search_finds_by_word_not_just_prefix(db):
    perception.log_event("file", "renamed quarterly_report_final.pdf")
    assert perception.query("quarterly"), "FTS is not matching interior words"


def test_search_misses_are_empty_not_errors(db):
    perception.log_event("screen", "something else entirely")
    assert perception.query("nothing_like_this") == []


def test_the_time_horizon_actually_bounds_results(db):
    """A `since_hours` that does not filter would quietly return everything —
    and an assistant confidently describing last month as today is worse than
    one that says it does not know."""
    now = time.time()
    perception.log_event("screen", "ancient event alpha", ts=now - 72 * 3600)
    perception.log_event("screen", "recent event alpha", ts=now - 60)

    recent = perception.query("alpha", since_hours=1.0)
    assert len(recent) == 1, f"time filter did not bound: {recent}"
    assert "recent" in recent[0]["content"]

    both = perception.query("alpha", since_hours=100.0)
    assert len(both) == 2


def test_recall_at_windows_around_a_moment(db):
    now = time.time()
    from datetime import datetime, timezone
    target = datetime.fromtimestamp(now, tz=timezone.utc)

    perception.log_event("screen", "inside the window", ts=now)
    perception.log_event("screen", "far outside", ts=now - 6 * 3600)

    hits = perception.recall_at(target.isoformat(), window_minutes=10)
    contents = " ".join(h["content"] for h in hits)
    assert "inside the window" in contents
    assert "far outside" not in contents


def test_recent_filters_by_source(db):
    perception.log_event("screen", "a screen thing")
    perception.log_event("file", "a file thing")

    screen = perception.recent(since_hours=1.0, source="screen")
    assert screen and all(h["source"] == "screen" for h in screen)
    assert len(perception.recent(since_hours=1.0)) == 2


def test_recent_respects_its_limit(db):
    for i in range(12):
        perception.log_event("screen", f"event {i}")
    assert len(perception.recent(since_hours=1.0, limit=5)) == 5


def test_logging_never_raises_into_a_watcher_thread(db, monkeypatch):
    """log_event is called from watcher threads. A raise there kills the
    watcher silently, so it swallows — pin that, since swallowing is only
    correct if it is deliberate."""
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(perception.longterm, "_conn", _boom)
    perception.log_event("screen", "should not raise")   # must not propagate
