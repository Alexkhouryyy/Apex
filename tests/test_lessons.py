"""Procedural memory: learning what works, without learning superstition.

Injecting remembered "lessons" into a system prompt is the standard way an agent
teaches itself nonsense — it notices a coincidence, writes it down, reads it back
forever, and grows confidently wrong. The design that prevents it is asymmetric:

    statistics PROPOSE  →  the model ARTICULATES  →  statistics RETIRE

so the model can phrase a lesson but never invent the subject of one, and nothing
survives on the strength of how convincing it sounds.

The invariant these tests exist to defend: **a lesson can never outlive its
evidence.**
"""
import time

import pytest

from agent import lessons, longterm, trajectory


@pytest.fixture(autouse=True)
def _db(test_db):
    trajectory.init_db()
    lessons.init_db()
    yield


def _events(tool, outcome, n, error_kind="", input_keys="", age_s=60):
    ts = time.time() - age_s
    with longterm._conn() as c:
        for _ in range(n):
            c.execute(
                "INSERT INTO tool_events (ts, session_id, tool, outcome, "
                "error_kind, duration_ms, recovered, input_keys) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ts, 1, tool, outcome, error_kind, 10, 0, input_keys))


# --- statistics propose --------------------------------------------------------

def test_a_real_failure_pattern_is_proposed():
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    found = [p for p in lessons.observe() if p["key"].endswith("kind:timeout")]
    assert found and found[0]["tool"] == "web_browse"
    assert found[0]["fails"] == 8 and found[0]["n"] == 10


def test_a_small_sample_is_not_a_pattern():
    """Three failures out of three is a bad afternoon, not knowledge."""
    _events("flaky", "error", 3, error_kind="timeout")
    assert lessons.observe() == []


def test_an_occasional_failure_is_not_a_pattern():
    _events("mostly_fine", "error", 2, error_kind="timeout")
    _events("mostly_fine", "ok", 30)
    assert lessons.observe() == []


def test_a_healthy_tool_produces_nothing():
    _events("solid", "ok", 50)
    assert lessons.observe() == []


def test_call_shape_patterns_are_detected():
    """Not just 'this tool is broken' but 'this tool breaks on this call shape'."""
    _events("read_file", "error", 6, error_kind="not_found", input_keys="path,offset")
    _events("read_file", "ok", 20, input_keys="path")
    shapes = [p for p in lessons.observe() if p["scope"] == "shape"]
    assert shapes and shapes[0]["detail"] == "path,offset"


def test_observations_outside_the_window_are_ignored():
    _events("old_tool", "error", 20, error_kind="timeout", age_s=40 * 86400)
    assert lessons.observe(days=14) == []


# --- the model articulates, but cannot invent ---------------------------------

def test_a_lesson_is_stored_with_its_evidence():
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    out = lessons.run(client=None)
    assert out["learned"] == 1
    row = lessons.active()[0]
    assert row["n"] == 10 and row["rate"] == pytest.approx(0.8, abs=0.01)


def test_the_model_only_phrases_measured_patterns(monkeypatch):
    """The articulator is handed measurements and returns wording. If it
    hallucinates extra lines the wording is discarded, never the measurement."""
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)

    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: "line one\nline two\nline three")
    lessons.run(client=object())
    text = lessons.active()[0]["text"]
    assert "line one" not in text          # count mismatch -> fall back
    assert "web_browse" in text            # the measurement survives


def test_articulation_failure_still_yields_a_lesson(monkeypatch):
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert lessons.run(client=object())["learned"] == 1
    assert "web_browse" in lessons.active()[0]["text"]


def test_a_good_phrasing_is_used(monkeypatch):
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    from agent import provider
    monkeypatch.setattr(provider, "complete",
                        lambda *a, **k: "web_browse times out often; use bash curl")
    lessons.run(client=object())
    assert lessons.active()[0]["text"] == "web_browse times out often; use bash curl"


# --- statistics retire: THE invariant ------------------------------------------

def test_a_lesson_dies_when_its_pattern_stops_holding():
    """The whole point. The tool gets fixed; the advice must not persist."""
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    lessons.run(client=None)
    assert len(lessons.active()) == 1

    # The tool starts working. Same window, overwhelming fresh success.
    _events("web_browse", "ok", 200)
    lessons.run(client=None)
    assert lessons.active() == [], "a lesson outlived its evidence"


def test_a_persuasive_lesson_dies_exactly_as_fast(monkeypatch):
    """Retirement is pure SQL, so wording buys a lesson nothing."""
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    from agent import provider
    monkeypatch.setattr(
        provider, "complete",
        lambda *a, **k: "Always avoid web_browse; it is fundamentally unreliable")
    lessons.run(client=object())
    assert len(lessons.active()) == 1

    _events("web_browse", "ok", 200)
    lessons.run(client=object())
    assert lessons.active() == []


def test_evidence_is_refreshed_while_the_pattern_holds():
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    lessons.run(client=None)
    before = lessons.active()[0]["n"]
    _events("web_browse", "error", 8, error_kind="timeout")
    lessons.run(client=None)
    assert lessons.active()[0]["n"] > before


def test_a_returning_problem_revives_its_lesson():
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    lessons.run(client=None)
    _events("web_browse", "ok", 200)
    lessons.run(client=None)
    assert lessons.active() == []

    # It breaks again, badly enough to clear the proposal bar on the full window.
    _events("web_browse", "error", 400, error_kind="timeout")
    out = lessons.run(client=None)
    assert out["revived"] == 1 and len(lessons.active()) == 1


def test_an_unused_tool_is_not_forgotten_immediately():
    """No observations this fortnight means 'untested', not 'fixed'. Retiring on
    silence would erase real knowledge over a quiet week."""
    _events("rare_tool", "error", 8, error_kind="timeout")
    _events("rare_tool", "ok", 2)
    lessons.run(client=None)
    with longterm._conn() as c:            # age the observations out of the window
        c.execute("UPDATE tool_events SET ts = ?", (time.time() - 20 * 86400,))
    lessons.run(client=None)
    assert len(lessons.active()) == 1, "knowledge dropped after a quiet spell"


def test_a_stale_lesson_is_eventually_retired():
    _events("rare_tool", "error", 8, error_kind="timeout")
    _events("rare_tool", "ok", 2)
    lessons.run(client=None)
    with longterm._conn() as c:
        c.execute("UPDATE tool_events SET ts = ?", (time.time() - 200 * 86400,))
        c.execute("UPDATE lessons SET confirmed_at = ?",
                  (time.time() - 200 * 86400,))
    lessons.run(client=None)
    assert lessons.active() == []


# --- what reaches the prompt ---------------------------------------------------

def test_prompt_block_is_empty_when_nothing_is_known():
    assert lessons.for_prompt() == ""


def test_prompt_block_shows_the_evidence():
    """A rule the agent cannot audit is a rule it should not be given."""
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    lessons.run(client=None)
    block = lessons.for_prompt()
    assert "web_browse" in block and "8/10 recent calls" in block


def test_prompt_block_is_capped():
    """Unbounded lessons is how a context window fills with folklore."""
    for i in range(20):
        _events(f"tool_{i}", "error", 8, error_kind="timeout")
        _events(f"tool_{i}", "ok", 2)
    lessons.run(client=None)
    assert len(lessons.active()) <= lessons.MAX_IN_PROMPT
    assert lessons.for_prompt().count("recent calls") <= lessons.MAX_IN_PROMPT


def test_lessons_reach_the_real_system_prompt():
    """Wiring test: the module could be perfect and still never be read — the
    failure mode this codebase keeps producing."""
    from agent import goals
    goals.init_db()
    _events("web_browse", "error", 8, error_kind="timeout")
    _events("web_browse", "ok", 2)
    lessons.run(client=None)
    assert "web_browse" in goals.active_goals_for_prompt()


def test_no_input_values_can_leak_into_a_lesson():
    """tool_events stores key NAMES only, so this holds by construction — pinned
    so a future change to input capture cannot quietly break it."""
    _events("read_file", "error", 8, error_kind="not_found",
            input_keys="path,secret_token")
    _events("read_file", "ok", 2, input_keys="path,secret_token")
    lessons.run(client=None)
    block = lessons.for_prompt()
    assert "path" in block or "read_file" in block
    with longterm._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tool_events)").fetchall()]
    assert "input_values" not in cols and "inputs" not in cols


# --- it must never break a conversation ---------------------------------------

def test_everything_fails_open(monkeypatch):
    class _Broken:
        def __enter__(self): raise RuntimeError("db gone")
        def __exit__(self, *a): return False

    monkeypatch.setattr(longterm, "_conn", lambda: _Broken())
    assert lessons.observe() == []
    assert lessons.active() == []
    assert lessons.for_prompt() == ""
    assert lessons.measure("tool:x|kind:y") is None


def test_a_malformed_key_does_not_raise():
    assert lessons.measure("garbage") is None
    assert lessons.measure("") is None


# --- a lesson must earn its slot ----------------------------------------------

def test_a_shape_lesson_is_not_a_restatement_of_the_tool_lesson():
    """send_email failing 6/7 times produced two lessons — one keyed on the
    error, one on the call shape — with identical numbers, spending two of six
    prompt slots to say one thing. A shape only earns a slot if it discriminates.
    """
    _events("send_email", "error", 6, error_kind="unconfigured",
            input_keys="to,subject")
    _events("send_email", "ok", 1, input_keys="to,subject")
    keys = [p["key"] for p in lessons.observe()]
    assert keys == ["tool:send_email|kind:unconfigured"]


def test_a_shape_that_really_does_discriminate_is_kept():
    """The case shape lessons exist for: the tool is fine, except like this."""
    _events("read_file", "error", 9, error_kind="not_found",
            input_keys="path,offset")
    _events("read_file", "ok", 1, input_keys="path,offset")
    _events("read_file", "ok", 60, input_keys="path")
    shapes = [p for p in lessons.observe() if p["scope"] == "shape"]
    assert shapes and shapes[0]["detail"] == "path,offset"


def test_the_fallback_wording_does_not_repeat_the_evidence():
    _events("web_browse", "error", 8, error_kind="timeout", input_keys="url")
    _events("web_browse", "ok", 2, input_keys="url")
    lessons.run(client=None)
    line = [l for l in lessons.for_prompt().splitlines() if "web_browse" in l][0]
    assert line.count("8/10") == 1
