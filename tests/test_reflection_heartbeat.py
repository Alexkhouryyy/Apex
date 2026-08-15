"""Memory consolidation must actually run.

`reflection.consolidate` had exactly one caller — the `reflect_now` tool — so
Apex's whole consolidation layer (reflections, and the preference digest that
rides in every system prompt via goals.active_goals_for_prompt) fired only when
the model happened to choose to. Built, tested, and dormant: the same pattern as
the orphaned research WebSocket events and the cortex that never ticked in
resident mode.

These tests pin the cadence, and — more importantly — pin that it survives a
restart. An in-memory timer would make the feature look enabled while never
reaching its interval on a machine that reboots daily.
"""
import time

import pytest

import config
from agent import longterm, reflection


@pytest.fixture(autouse=True)
def _fresh(test_db, monkeypatch):
    monkeypatch.setattr(config, "REFLECTION_INTERVAL_HOURS", 6, raising=False)
    monkeypatch.setattr(reflection, "_last_run_memory", 0.0, raising=False)
    from agent import world_model
    world_model.init_db()
    yield


def _set_last_run(ts: float) -> None:
    with longterm._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO world_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (reflection._LAST_RUN_KEY, "1", ts),
        )


# --- the cadence --------------------------------------------------------------

def test_due_when_it_has_never_run():
    """The state Apex actually shipped in — no consolidation, ever."""
    assert reflection.is_due() is True


def test_not_due_immediately_after_a_run():
    _set_last_run(time.time())
    assert reflection.is_due() is False


def test_due_again_once_the_interval_passes():
    _set_last_run(time.time() - 7 * 3600)
    assert reflection.is_due() is True


def test_interval_is_configurable(monkeypatch):
    _set_last_run(time.time() - 2 * 3600)
    assert reflection.is_due() is False
    monkeypatch.setattr(config, "REFLECTION_INTERVAL_HOURS", 1, raising=False)
    assert reflection.is_due() is True


def test_the_timestamp_survives_a_restart():
    """THE reason this is persisted rather than a module global: a process that
    restarts more often than the interval would otherwise never consolidate,
    and the feature would look enabled while doing nothing."""
    _set_last_run(time.time())
    assert reflection.is_due() is False
    # Clear the in-process fallback so only the persisted value can answer.
    reflection._last_run_memory = 0.0
    assert reflection.is_due() is False   # still remembered, from the DB


# --- running ------------------------------------------------------------------

def test_it_runs_when_due(monkeypatch):
    calls = []
    monkeypatch.setattr(reflection, "consolidate",
                        lambda c, hours=24: calls.append(hours) or {"created": 2})
    out = reflection.consolidate_if_due(object())
    assert out == {"created": 2} and calls == [24]


def test_it_skips_when_not_due(monkeypatch):
    _set_last_run(time.time())
    calls = []
    monkeypatch.setattr(reflection, "consolidate",
                        lambda c, hours=24: calls.append(1) or {})
    assert reflection.consolidate_if_due(object()) is None
    assert calls == []


def test_running_marks_the_timestamp_before_the_work(monkeypatch):
    """A pass that crashes must wait out the interval, not retry in a hot loop
    burning a model call every 15 seconds."""
    seen = {}

    def _boom(c, hours=24):
        seen["due_during_run"] = reflection.is_due()
        raise RuntimeError("model down")

    monkeypatch.setattr(reflection, "consolidate", _boom)
    out = reflection.consolidate_if_due(object())
    assert out["error"] == "model down"
    assert seen["due_during_run"] is False      # already marked
    assert reflection.is_due() is False         # and stays marked after failing


def test_a_failure_never_raises(monkeypatch):
    """This runs on a background thread in the awareness loop; an exception
    escaping would kill consolidation for the life of the process."""
    monkeypatch.setattr(reflection, "consolidate",
                        lambda c, hours=24: (_ for _ in ()).throw(ValueError("x")))
    assert reflection.consolidate_if_due(object())["created"] == 0


def test_no_client_means_no_run(monkeypatch):
    calls = []
    monkeypatch.setattr(reflection, "consolidate",
                        lambda c, hours=24: calls.append(1) or {})
    assert reflection.consolidate_if_due(None) is None
    assert calls == []


def test_overlapping_passes_are_refused(monkeypatch):
    """Consolidation is a full model call; two at once would double-spend and
    could interleave writes."""
    started = []

    def _slow(c, hours=24):
        started.append(1)
        # Re-entrant call while the first is still inside consolidate().
        assert reflection.consolidate_if_due(object()) is None
        return {"created": 0}

    monkeypatch.setattr(reflection, "consolidate", _slow)
    reflection.consolidate_if_due(object())
    assert started == [1]


# --- it must be wired into the loop that both entry points share --------------

def test_the_awareness_loop_triggers_consolidation():
    """build_monitor is shared by main.py and app/resident.py precisely so this
    kind of thing cannot run in one entry point and not the other — which is
    exactly what happened to the cortex."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agent/awareness.py").read_text()
    loop = src[src.index("def _review_loop"):src.index("def build_monitor")]
    assert "consolidate_if_due" in loop
    # Before the review `continue`, or it would inherit the review cadence.
    assert loop.index("consolidate_if_due") < loop.index("_last_review < self.review_interval")
    # Off-thread: a model call must not stall the 15s Guardian checks.
    assert "MemoryConsolidation" in loop


def test_a_persistence_failure_cannot_cause_a_spend_loop(monkeypatch):
    """If the timestamp cannot be stored, is_due() would be True on every 15s
    tick and each tick would spend a full model call. Degrading to a
    per-process clock is the acceptable failure; an unbounded loop is not.
    """
    monkeypatch.setattr(reflection, "_last_run_memory", 0.0, raising=False)

    class _Broken:
        def __enter__(self): raise RuntimeError("db gone")
        def __exit__(self, *a): return False

    monkeypatch.setattr(longterm, "_conn", lambda: _Broken())
    runs = []
    monkeypatch.setattr(reflection, "consolidate",
                        lambda c, hours=24: runs.append(1) or {"created": 0})

    assert reflection.consolidate_if_due(object()) is not None    # first runs
    assert reflection.consolidate_if_due(object()) is None        # second does not
    assert reflection.consolidate_if_due(object()) is None
    assert runs == [1], "consolidation ran repeatedly with a broken DB"
