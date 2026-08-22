"""Did Apex's advice actually work, and did its nudges actually help?

Two rows in docs/APEX_GAP_ANALYSIS.md were UNPROVEN for a different reason than
the rest: not untested, unmeasured.

  §15 outcome learning  — Apex measured 👍/👎 and called it outcomes. Liking an
                          answer and the answer working are different variables,
                          and they come apart exactly where it matters: fluent
                          confident wrong advice rates well at the time.
  §16 blocker adjustment — Apex proposed things for stalled goals and never
                          checked whether proposing changed anything.

Both measurements are built to be able to say "no". A metric that can only
flatter the feature it measures is decoration.
"""
from __future__ import annotations

import json
import time

import pytest

from agent import initiative, longterm, outcomes


@pytest.fixture
def db(test_db):
    outcomes.init_db()
    initiative.init_db()
    from agent import goals, approvals
    goals.init_db()
    approvals.init_db()
    return test_db


# ── §15: real-world outcomes ──────────────────────────────────────────────────

def test_an_outcome_survives_and_counts(db):
    outcomes.record("Use CV version B", "Got 3 interviews", success=True,
                    domain="job-search", impact=0.8)
    r = outcomes.recommendation_accuracy()
    assert r["recorded"] == 1 and r["worked"] == 1


def test_a_rate_is_withheld_until_there_is_enough(db):
    """Two wins out of two is not a 100% track record, and reporting it as one
    is how a number starts lying."""
    for i in range(2):
        outcomes.record(f"advice {i}", "worked", success=True)
    r = outcomes.recommendation_accuracy()
    assert r["rate"] is None
    assert "need" in r["note"]


def test_a_rate_appears_once_there_is(db):
    for i in range(4):
        outcomes.record(f"advice {i}", "worked", success=True)
    outcomes.record("bad advice", "did not work", success=False)
    r = outcomes.recommendation_accuracy()
    assert r["decided"] == 5
    assert r["rate"] == 0.8


def test_it_can_report_that_the_advice_was_bad(db):
    """The measurement has to be able to condemn the thing it measures."""
    for i in range(5):
        outcomes.record(f"advice {i}", "made it worse", success=False)
    r = outcomes.recommendation_accuracy()
    assert r["rate"] == 0.0
    assert r["worked"] == 0


def test_undecided_outcomes_are_held_out_not_folded_in(db):
    """Most real outcomes are partial. Coercing them to a boolean would make the
    rate measure how decisively things get logged."""
    for i in range(5):
        outcomes.record(f"advice {i}", "worked", success=True)
    outcomes.record("unclear one", "hard to say", success=None)
    r = outcomes.recommendation_accuracy()
    assert r["undecided"] == 1
    assert r["decided"] == 5
    assert r["rate"] == 1.0


def test_domains_are_measured_separately(db):
    for i in range(5):
        outcomes.record(f"job advice {i}", "worked", success=True, domain="job-search")
    for i in range(5):
        outcomes.record(f"code advice {i}", "broke", success=False, domain="code")

    rates = {d["domain"]: d["rate"] for d in outcomes.by_domain()}
    assert rates["job-search"] == 1.0
    assert rates["code"] == 0.0


def test_an_outcome_needs_both_halves(db):
    for bad in [("", "something happened"), ("recommended something", "")]:
        with pytest.raises(ValueError):
            outcomes.record(*bad)


def test_coverage_reports_how_little_is_recorded(db):
    """The honest headline. A 100% accuracy over four outcomes reads like a
    track record unless this number sits next to it."""
    outcomes.record("advice", "worked", success=True)
    c = outcomes.coverage()
    assert c["outcomes_recorded"] == 1
    assert "recorded by you, not observed" in c["note"]


def test_the_agent_can_actually_record_one(db, monkeypatch):
    """A measurement with no way to feed it is the twelfth 'built but never
    ran'."""
    import config
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    from agent import core
    assert any(t["name"] == "record_outcome" for t in core.AgentCore()._all_tools())

    out = core._execute_tool("record_outcome", {
        "recommendation": "Use version B", "result": "Two interviews",
        "success": True, "domain": "job-search"})
    assert "Recorded outcome" in out
    assert outcomes.recommendation_accuracy()["worked"] == 1


# ── §16: did the nudging help? ────────────────────────────────────────────────

def _stalled_goal(title="a goal", updated=None):
    from agent import goals
    gid = goals.set_goal(title=title, horizon="week")
    if isinstance(gid, str):
        with longterm._conn() as c:
            gid = c.execute("SELECT id FROM goals ORDER BY id DESC LIMIT 1").fetchone()[0]
    if updated:
        with longterm._conn() as c:
            c.execute("UPDATE goals SET updated_at = ? WHERE id = ?", (updated, gid))
    return gid


def _proposal(goal_id, status, ts):
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO staged_writes (ts, kind, summary, payload_json, status) "
            "VALUES (?,?,?,?,?)",
            (ts, initiative.KIND, "revive it",
             json.dumps({"evidence_key": f"stalled:{goal_id}"}), status))


def _progress(goal_id, ts, note="did something"):
    with longterm._conn() as c:
        c.execute("INSERT INTO goal_progress (goal_id, ts, note) VALUES (?,?,?)",
                  (goal_id, ts, note))


def test_too_few_cases_says_so_rather_than_guessing(db):
    now = time.time()
    gid = _stalled_goal()
    _proposal(gid, "approved", now - 86400)
    _progress(gid, now)
    r = initiative.intervention_effect()
    assert r["lift"] is None or "Too few" in r["verdict"]


def test_it_detects_that_the_nudging_is_not_working(db):
    """The result that matters. Selection bias favours the opposite finding, so
    a null here is trustworthy — and the verdict says exactly that."""
    now = time.time()
    for i in range(4):
        g = _stalled_goal(f"accepted {i}")
        _proposal(g, "approved", now - 86400)          # accepted, no progress after
    for i in range(4):
        g = _stalled_goal(f"declined {i}")
        _proposal(g, "rejected", now - 86400)
        _progress(g, now)                              # declined, progressed anyway

    r = initiative.intervention_effect()
    assert r["accepted"]["rate"] == 0.0
    assert r["not_accepted"]["rate"] == 1.0
    assert r["lift"] < 0
    assert "not helping" in r["verdict"]


def test_it_detects_that_the_nudging_might_be_working(db):
    now = time.time()
    for i in range(4):
        g = _stalled_goal(f"accepted {i}")
        _proposal(g, "approved", now - 86400)
        _progress(g, now)
    for i in range(4):
        g = _stalled_goal(f"declined {i}")
        _proposal(g, "rejected", now - 86400)

    r = initiative.intervention_effect()
    assert r["lift"] > 0
    assert "Suggestive" in r["verdict"]


def test_a_positive_result_never_claims_causation(db):
    now = time.time()
    for i in range(6):
        g = _stalled_goal(f"g{i}")
        _proposal(g, "approved", now - 86400)
        _progress(g, now)
    r = initiative.intervention_effect()
    low = (r["verdict"] + " " + r["caveat"]).lower()
    assert "not causal" in low or "consistent with" in low
    assert "observational" in low


def test_progress_before_the_proposal_does_not_count(db):
    """Otherwise a goal that was already moving would credit the proposal."""
    now = time.time()
    g = _stalled_goal()
    _progress(g, now - 200000)                  # before
    _proposal(g, "approved", now - 86400)
    r = initiative.intervention_effect()
    assert r["accepted"]["moved"] == 0


def test_closing_a_stalled_goal_counts_as_resolved(db):
    """The proposal offers "revive it or close it". Deciding to drop a goal is
    the proposal working, not failing."""
    now = time.time()
    g = _stalled_goal()
    _proposal(g, "approved", now - 86400)
    with longterm._conn() as c:
        c.execute("UPDATE goals SET status = 'abandoned' WHERE id = ?", (g,))
    r = initiative.intervention_effect()
    assert r["accepted"]["moved"] == 1


def test_lesson_proposals_are_excluded(db):
    """Only stalled-goal proposals have a goal that could move. A lesson
    proposal has nothing to measure and would dilute the rate."""
    now = time.time()
    with longterm._conn() as c:
        c.execute(
            "INSERT INTO staged_writes (ts, kind, summary, payload_json, status) "
            "VALUES (?,?,?,?,?)",
            (now - 86400, initiative.KIND, "fix a failure",
             json.dumps({"evidence_key": "lesson:tool|send_email"}), "approved"))
    r = initiative.intervention_effect()
    assert r["accepted"]["n"] == 0
