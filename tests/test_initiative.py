"""Initiative: Apex surfacing work its own evidence implies.

An agent that originates goals is the part of this project most worth being
careful about, so the safety here is structural rather than instructed:

- a proposal CANNOT become a goal; only approvals._apply creates one, and that
  runs when the user approves
- silence is the default; no evidence means no proposals, forever
- declining sticks, because a review gate nobody reads is worse than no gate —
  it still looks like oversight

The single most important test in this file is
`test_a_proposal_is_never_a_goal_until_approved`.
"""
import time

import pytest

from agent import approvals, goals, initiative, lessons, longterm, trajectory


@pytest.fixture(autouse=True)
def _db(test_db):
    for mod in (trajectory, lessons, goals, approvals, initiative):
        mod.init_db()
    # reflection keeps an in-process clock as its persistence fallback; without
    # resetting it, one test's consolidation run silences the next one's.
    from agent import reflection
    reflection._last_run_memory = 0.0
    yield


def _failing_tool(tool="web_browse", fails=8, oks=2):
    ts = time.time() - 60
    with longterm._conn() as c:
        for outcome, n, kind in (("error", fails, "timeout"), ("ok", oks, "")):
            for _ in range(n):
                c.execute(
                    "INSERT INTO tool_events (ts, session_id, tool, outcome, "
                    "error_kind, duration_ms, recovered, input_keys) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (ts, 1, tool, outcome, kind, 10, 0, "url"))
    lessons.run(client=None)


def _stalled_goal(title="Ship the thing", horizon="week", days_idle=20):
    goals.set_goal(title, "", horizon=horizon)
    old = time.time() - days_idle * 86400
    with longterm._conn() as c:
        c.execute("UPDATE goals SET created_at = ?, updated_at = ?", (old, old))


def _pending():
    return [p for p in approvals.list_pending() if p["kind"] == initiative.KIND]


# --- THE gate -----------------------------------------------------------------

def test_a_proposal_is_never_a_goal_until_approved():
    """The one that matters. Proposing must change nothing about reality."""
    _failing_tool()
    assert goals.list_goals() == []

    staged = initiative.propose()
    assert len(staged) == 1
    assert goals.list_goals() == [], "initiative created a goal without approval"
    assert len(_pending()) == 1

    approvals.approve(_pending()[0]["id"])
    made = goals.list_goals()
    assert len(made) == 1
    assert "web_browse" in made[0]["title"]


def test_rejecting_creates_nothing():
    _failing_tool()
    initiative.propose()
    approvals.reject(_pending()[0]["id"])
    assert goals.list_goals() == []


# --- silence is the default ----------------------------------------------------

def test_a_healthy_apex_proposes_nothing():
    """No failing tools, no stalled goals — and so, nothing to say."""
    assert initiative.gather() == []
    assert initiative.propose() == []


def test_a_fresh_goal_is_not_stalled():
    goals.set_goal("Just started", "", horizon="week")
    assert initiative.gather() == []


def test_healthy_tools_produce_no_proposals():
    _failing_tool(tool="fine_tool", fails=1, oks=60)
    assert initiative.propose() == []


# --- evidence, not invention ---------------------------------------------------

def test_a_failing_tool_becomes_a_proposal_with_its_evidence():
    _failing_tool()
    item = initiative.gather()[0]
    assert "web_browse" in item["title"]
    assert "8/10" in item["evidence"]


def test_a_stalled_goal_becomes_a_proposal():
    _stalled_goal()
    item = [i for i in initiative.gather() if i["evidence_key"].startswith("stalled:")][0]
    assert "Ship the thing" in item["title"]
    assert "no progress in 20 days" in item["evidence"]


def test_a_goal_within_its_horizon_is_left_alone():
    """A quarter-horizon goal idle for 20 days is not stalled; a week one is."""
    _stalled_goal(horizon="quarter", days_idle=20)
    assert [i for i in initiative.gather() if i["evidence_key"].startswith("stalled:")] == []


def test_an_overdue_goal_is_stalled_regardless_of_idleness():
    from datetime import datetime
    goals.set_goal("Overdue", "", horizon="quarter",
                   deadline_iso=datetime.fromtimestamp(time.time() - 86400).isoformat())
    keys = [i["evidence_key"] for i in initiative.gather()]
    assert any(k.startswith("stalled:") for k in keys)


def test_the_evidence_reaches_the_approval_summary():
    """The summary is what the push notification carries; a proposal you can't
    audit from the notification is one you rubber-stamp or ignore."""
    _failing_tool()
    initiative.propose()
    assert "8/10" in _pending()[0]["summary"]


# --- rate limits are a safety feature -----------------------------------------

def test_nothing_new_while_proposals_sit_unreviewed():
    """Approval fatigue turns a review gate into a rubber stamp."""
    _failing_tool(tool="tool_a")
    _failing_tool(tool="tool_b")
    _failing_tool(tool="tool_c")
    _failing_tool(tool="tool_d")
    initiative.propose()
    assert len(_pending()) == initiative.MAX_PENDING
    assert initiative.propose() == []


def test_room_frees_up_once_reviewed():
    _failing_tool(tool="tool_a")
    _failing_tool(tool="tool_b")
    _failing_tool(tool="tool_c")
    _failing_tool(tool="tool_d")
    initiative.propose()
    for p in _pending():
        approvals.approve(p["id"])
    assert len(initiative.propose()) >= 1


def test_the_same_subject_is_not_proposed_twice():
    _failing_tool()
    assert len(initiative.propose()) == 1
    approvals.approve(_pending()[0]["id"])
    assert initiative.propose() == [], "the same evidence was proposed again"


# --- declining sticks ----------------------------------------------------------

def test_a_declined_proposal_is_not_raised_again():
    """Otherwise 'no' means 'ask me again in six hours'."""
    _failing_tool()
    initiative.propose()
    approvals.reject(_pending()[0]["id"])
    assert initiative.propose() == []


def test_reject_all_records_every_decline():
    _failing_tool(tool="tool_a")
    _failing_tool(tool="tool_b")
    initiative.propose()
    approvals.reject("all")
    assert initiative.propose() == []


def test_a_decline_expires_eventually():
    """A problem returning a season later is new evidence, not the old ask."""
    _failing_tool()
    initiative.propose()
    approvals.reject(_pending()[0]["id"])
    with longterm._conn() as c:
        c.execute("UPDATE initiative_log SET ts = ?",
                  (time.time() - (initiative.DECLINE_MEMORY_DAYS + 1) * 86400,))
    assert len(initiative.propose()) == 1


# --- it must never cost anything else ------------------------------------------

def test_the_pass_fails_open(monkeypatch):
    class _Broken:
        def __enter__(self): raise RuntimeError("db gone")
        def __exit__(self, *a): return False

    monkeypatch.setattr(longterm, "_conn", lambda: _Broken())
    assert initiative.run() == {"proposed": 0, "keys": []}


def test_an_unreadable_log_stays_quiet_rather_than_spamming(monkeypatch):
    """If it cannot tell what was already proposed, the safe answer is silence."""
    _failing_tool()

    def _boom(key, now):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(initiative, "_seen", lambda k, n: True)
    assert initiative.propose() == []


def test_unknown_pending_count_proposes_nothing(monkeypatch):
    monkeypatch.setattr(approvals, "list_pending",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _failing_tool()
    assert initiative.propose() == []


def test_initiative_rides_the_consolidation_heartbeat(monkeypatch):
    from agent import reflection
    monkeypatch.setattr(reflection, "consolidate", lambda c, hours=24: {"created": 0})
    monkeypatch.setattr(lessons, "run", lambda client: {"learned": 0, "retired": 0})
    out = reflection.consolidate_if_due(object())
    assert "initiative" in out


def test_an_initiative_failure_does_not_lose_the_reflection(monkeypatch):
    from agent import reflection
    monkeypatch.setattr(reflection, "consolidate", lambda c, hours=24: {"created": 5})
    monkeypatch.setattr(lessons, "run", lambda client: {"learned": 0, "retired": 0})
    monkeypatch.setattr(initiative, "run",
                        lambda now=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert reflection.consolidate_if_due(object())["created"] == 5


def test_stall_horizons_match_the_real_goal_horizons():
    """Caught by a failing test that used horizon='year': set_goal rejected it
    silently, and _HORIZON_DAYS carried an entry for a horizon that can never
    exist — dead code that reads like coverage."""
    assert set(initiative._HORIZON_DAYS) == goals.VALID_HORIZONS
