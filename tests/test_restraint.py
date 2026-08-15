"""Restraint: learning when *not* to interrupt.

An assistant that interrupts badly gets muted, and a muted assistant is worth
zero however capable it is. So Apex holds low-stakes notifications until a
moment you actually respond in.

Four rules keep that from becoming a bug, and there is a test for each:

  1. urgent always gets through
  2. held is never dropped
  3. cold start is permissive — it learns to be quieter, never starts quiet
  4. it fails open; a bug in restraint must never silence Apex

Rule 4 makes this feature unusually easy to get wrong in an invisible way: every
failure mode degrades to "behaves exactly as before". The tests therefore assert
that holding *happens* when it should, not merely that nothing crashes.
"""
import json
import time
from datetime import datetime

import pytest

import config
from agent import longterm, notify, restraint


@pytest.fixture(autouse=True)
def _fresh(test_db, monkeypatch):
    monkeypatch.setattr(config, "RESTRAINT_ENABLED", True, raising=False)
    monkeypatch.setattr(restraint, "_ready", False, raising=False)
    yield


def _quiet_hour(bucket_ts, pings=12, engaged=0):
    """Write a history where pings at this moment were mostly ignored."""
    b = restraint.bucket(bucket_ts)
    restraint._ensure_db()
    with longterm._conn() as c:
        for i in range(pings):
            c.execute(
                "INSERT INTO interruptions (ts, kind, priority, bucket, engaged) "
                "VALUES (?,?,?,?,?)",
                (bucket_ts - i * 86400, "test", "normal", b,
                 1 if i < engaged else 0))
    return b


def _at_hour(hour=3, weekday=2):
    """A timestamp at a fixed weekday/hour, so bucket maths is deterministic."""
    base = datetime(2026, 8, 12, hour, 30)      # a Wednesday
    return base.timestamp()


# --- rule 1: urgent always gets through ---------------------------------------

def test_urgent_is_never_held_however_bad_the_moment():
    ts = _at_hour()
    _quiet_hour(ts)
    hold, _ = restraint.should_hold("guardian", "high", now=ts)
    assert hold is False


def test_a_normal_message_at_the_same_bad_moment_is_held():
    """The contrast that proves the previous test means something."""
    ts = _at_hour()
    _quiet_hour(ts)
    hold, why = restraint.should_hold("timecapsule", "normal", now=ts)
    assert hold is True
    assert "engagement" in why


# --- rule 2: held is never dropped --------------------------------------------

def test_user_activity_releases_everything_immediately():
    """The whole point: the message arrives when you can actually take it."""
    restraint.hold({"title": "later", "body": "b", "kind": "info"})
    out = restraint.due(user_active=True)
    assert len(out) == 1 and out[0]["title"] == "later"
    assert restraint.held_count() == 0


def test_a_receptive_moment_releases_held_messages():
    ts = _at_hour(hour=10)
    restraint.hold({"title": "later"}, now=ts)
    # 10:00 has a good record, so the moment itself is enough.
    _quiet_hour(ts, pings=12, engaged=12)
    assert len(restraint.due(now=ts)) == 1


def test_nothing_is_held_past_the_ceiling():
    """Even at a permanently bad hour, a message escapes eventually."""
    ts = _at_hour()
    _quiet_hour(ts)
    restraint.hold({"title": "eventually"}, now=ts)
    later = ts + restraint.MAX_HOLD_S + 1
    assert len(restraint.due(now=later)) == 1


def test_a_bad_moment_alone_does_not_release():
    ts = _at_hour()
    _quiet_hour(ts)
    restraint.hold({"title": "waiting"}, now=ts)
    assert restraint.due(now=ts + 60) == []
    assert restraint.held_count() == 1      # still queued, not lost


def test_a_released_message_is_removed_from_the_queue():
    restraint.hold({"title": "once"})
    restraint.due(user_active=True)
    assert restraint.due(user_active=True) == []


def test_an_unreadable_held_row_does_not_wedge_the_queue():
    """One corrupt payload must not block every message behind it."""
    restraint._ensure_db()
    with longterm._conn() as c:
        c.execute("INSERT INTO held_notifications (ts, payload, release_at) "
                  "VALUES (?,?,?)", (time.time(), "{not json", time.time()))
    restraint.hold({"title": "good one"})
    out = restraint.due(user_active=True)
    assert [o["title"] for o in out] == ["good one"]
    assert restraint.held_count() == 0


# --- rule 3: cold start is permissive -----------------------------------------

def test_it_sends_everything_before_it_has_learned_anything():
    """An agent that starts silent is indistinguishable from a broken one."""
    hold, why = restraint.should_hold("info", "normal")
    assert hold is False and "samples" in why


def test_two_ignored_pings_cannot_silence_an_hour():
    """Below MIN_SAMPLES a bad run is noise, not a habit."""
    ts = _at_hour()
    _quiet_hour(ts, pings=2, engaged=0)
    assert restraint.should_hold("info", "normal", now=ts)[0] is False


def test_the_threshold_is_where_it_says_it_is():
    ts = _at_hour()
    _quiet_hour(ts, pings=restraint.MIN_SAMPLES - 1, engaged=0)
    assert restraint.should_hold("info", "normal", now=ts)[0] is False
    _quiet_hour(ts, pings=1, engaged=0)      # one more tips it over
    assert restraint.should_hold("info", "normal", now=ts)[0] is True


def test_a_moment_you_engage_with_stays_open():
    ts = _at_hour(hour=9)
    _quiet_hour(ts, pings=20, engaged=18)
    assert restraint.should_hold("info", "normal", now=ts)[0] is False


# --- rule 4: it fails open -----------------------------------------------------

def test_a_broken_database_sends_rather_than_silences(monkeypatch):
    class _Broken:
        def __enter__(self): raise RuntimeError("db gone")
        def __exit__(self, *a): return False

    monkeypatch.setattr(longterm, "_conn", lambda: _Broken())
    assert restraint.should_hold("info", "normal")[0] is False


def test_disabling_it_sends_everything(monkeypatch):
    ts = _at_hour()
    _quiet_hour(ts)
    monkeypatch.setattr(config, "RESTRAINT_ENABLED", False, raising=False)
    assert restraint.should_hold("info", "normal", now=ts)[0] is False


def test_the_notifier_sends_when_restraint_raises(monkeypatch):
    sent = []
    n = notify.Notifier()
    monkeypatch.setattr(n, "_deliver", lambda p: sent.append(p))
    monkeypatch.setattr(restraint, "should_hold",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    n.notify("t", "b")
    assert len(sent) == 1


def test_a_failure_to_hold_still_delivers(monkeypatch):
    """If parking the message fails, send it — never let it evaporate."""
    sent = []
    n = notify.Notifier()
    monkeypatch.setattr(n, "_deliver", lambda p: sent.append(p))
    monkeypatch.setattr(restraint, "should_hold", lambda *a, **k: (True, "quiet"))
    monkeypatch.setattr(restraint, "hold",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no room")))
    n.notify("t", "b")
    assert len(sent) == 1, "a message vanished when holding failed"


# --- engagement is measured, not assumed --------------------------------------

def test_engagement_is_scored_from_real_user_turns():
    ts = time.time() - 3600
    restraint.record("info", "normal", ts=ts)
    with longterm._conn() as c:
        c.execute("INSERT INTO turn_log (ts, session_id, turn_index, role, "
                  "content_json, tool_calls_json) VALUES (?,?,?,?,?,?)",
                  (ts + 60, 1, 0, "user", json.dumps({"text": "hi"}), "[]"))
    assert restraint.score_pending() == 1
    rate, n = restraint.receptiveness(restraint.bucket(ts))
    assert n == 1 and rate == 1.0


def test_an_ignored_ping_scores_zero():
    ts = time.time() - 3600
    restraint.record("info", "normal", ts=ts)
    restraint.score_pending()
    rate, n = restraint.receptiveness(restraint.bucket(ts))
    assert n == 1 and rate == 0.0


def test_activity_long_after_the_ping_is_not_credited():
    ts = time.time() - 6 * 3600
    restraint.record("info", "normal", ts=ts)
    with longterm._conn() as c:
        c.execute("INSERT INTO turn_log (ts, session_id, turn_index, role, "
                  "content_json, tool_calls_json) VALUES (?,?,?,?,?,?)",
                  (ts + restraint.ENGAGE_WINDOW_S + 600, 1, 0, "user",
                   json.dumps({"text": "hi"}), "[]"))
    restraint.score_pending()
    assert restraint.receptiveness(restraint.bucket(ts))[0] == 0.0


def test_a_ping_too_recent_to_judge_is_left_unscored():
    restraint.record("info", "normal", ts=time.time() - 60)
    assert restraint.score_pending() == 0


# --- the init bug this shipped with -------------------------------------------

def test_it_works_without_an_explicit_init_db():
    """Restraint shipped absent from main.py's init block. Because every query
    fails open, the tables never existed and it held nothing forever while
    looking like it worked. This asserts a hold HAPPENS on a fresh DB — a test
    that only checked for the absence of a crash would have passed throughout.
    """
    ts = _at_hour()
    b = restraint.bucket(ts)
    restraint._ready = False
    with longterm._conn() as c:            # no init_db() anywhere in this test
        c.execute("DROP TABLE IF EXISTS interruptions")
        c.execute("DROP TABLE IF EXISTS held_notifications")
    restraint._ready = False
    for i in range(12):
        restraint.record("test", "normal", ts=ts - i * 86400)
    with longterm._conn() as c:
        c.execute("UPDATE interruptions SET engaged = 0, bucket = ?", (b,))
    assert restraint.should_hold("info", "normal", now=ts)[0] is True


# --- it has to be able to explain itself --------------------------------------

def test_explain_says_it_is_still_learning():
    assert "learning" in restraint.explain()


def test_explain_reports_holding_and_the_backlog():
    ts = _at_hour()
    _quiet_hour(ts)
    restraint.hold({"title": "x"}, now=ts)
    text = restraint.explain()
    assert "waiting" in text


def test_the_dashboard_can_answer_why_didnt_you_tell_me():
    """An unexplainable hold is indistinguishable from a dropped message."""
    from dashboard import server
    paths = {r.path for r in server.app.routes if hasattr(r, "path")}
    assert "/api/restraint" in paths
    assert "/api/restraint/release" in paths   # manual override when it is wrong


# --- the gate covers every producer -------------------------------------------

def test_every_interruption_path_goes_through_the_gate():
    """Guardian, time capsule, scheduler, reflection, rollback and approvals all
    call notify(); gating there is what makes this one insertion sufficient."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agent/notify.py").read_text()
    fn = src[src.index("    def notify(self"):src.index("    def _deliver(self")]
    assert "restraint.should_hold" in fn


def test_released_messages_are_not_re_judged():
    """A held message that had to pass restraint again could be held forever."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agent/notify.py").read_text()
    deliver = src[src.index("    def _deliver(self"):src.index("    # -- sinks")]
    assert "should_hold" not in deliver
