"""IoT event filtering — the half of the watcher that can be tested here.

`agent/iot_watcher.py` is constructed at `agent/awareness.py:276` and pushes
Home Assistant state changes into the awareness log, where the 60s reviewer
decides whether to interrupt you. It was an UNPROVEN row.

The WebSocket half needs a Home Assistant instance and stays unproven — the gap
analysis row says PARTIAL for that reason rather than WORKS. The filtering half
decides what is allowed to interrupt you, and it is pure once lifted out of the
socket loop, which is what `decide_event` now is.
"""
from __future__ import annotations

import pytest

import config
from agent import iot_watcher


def _event(entity="light.kitchen", old="off", new="on"):
    return {"type": "event", "event": {"data": {
        "entity_id": entity,
        "old_state": {"state": old},
        "new_state": {"state": new},
    }}}


@pytest.fixture(autouse=True)
def kill_switch_on(monkeypatch):
    from agent import iot
    monkeypatch.setattr(iot, "is_enabled", lambda: True)


def test_a_real_state_change_is_logged(monkeypatch):
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    line = iot_watcher.decide_event(_event())
    assert line, "a genuine state change was dropped"
    assert "light.kitchen" in line and "off" in line and "on" in line


def test_an_unchanged_state_is_dropped(monkeypatch):
    """Home Assistant re-emits unchanged states. Logging them would bury the
    real changes under noise the reviewer then reads every 60 seconds."""
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    assert iot_watcher.decide_event(_event(old="on", new="on")) is None


def test_the_allowlist_excludes_everything_else(monkeypatch):
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES",
                        ["light.kitchen"], raising=False)
    assert iot_watcher.decide_event(_event("light.kitchen")) is not None
    assert iot_watcher.decide_event(_event("camera.bedroom")) is None, (
        "an entity outside the allowlist reached the awareness log"
    )


def test_an_empty_allowlist_means_everything(monkeypatch):
    """Documented behaviour: empty allowlist watches all entities."""
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    assert iot_watcher.decide_event(_event("anything.at_all")) is not None


def test_the_kill_switch_stops_everything(monkeypatch):
    """Checked per event so turning IoT off takes effect immediately, not at
    the next reconnect."""
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    from agent import iot
    monkeypatch.setattr(iot, "is_enabled", lambda: False)
    assert iot_watcher.decide_event(_event()) is None


@pytest.mark.parametrize("msg", [
    {},
    {"type": "auth_ok"},
    {"type": "result", "success": True},
    {"type": "event"},                       # no data at all
    {"type": "event", "event": {}},
    {"type": "event", "event": {"data": {}}},
    None,
    "not a dict",
])
def test_malformed_messages_are_dropped_not_raised(monkeypatch, msg):
    """This runs in a socket thread. A raise kills the watcher silently."""
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    assert iot_watcher.decide_event(msg) is None


def test_missing_states_do_not_produce_a_junk_line(monkeypatch):
    monkeypatch.setattr(config, "IOT_AWARENESS_ENTITIES", [], raising=False)
    msg = {"type": "event", "event": {"data": {"entity_id": "light.x"}}}
    # Both sides unknown means no change, so nothing to say.
    assert iot_watcher.decide_event(msg) is None
