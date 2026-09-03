"""Tests for Phase 8 IoT integration — kill switch, channel dispatch, safety, and config."""
import json
import importlib
from unittest.mock import MagicMock, patch
import pytest

from agent import longterm


# ---------------------------------------------------------------------------
# Kill switch — agent/iot.py
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_disabled_when_env_flag_off(self, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", False)
        import agent.iot as iot
        importlib.reload(iot)
        assert iot.is_enabled() is False

    def test_enabled_when_env_flag_on_and_db_defaults_on(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", True)
        import agent.iot as iot
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)
        iot.init_db()
        assert iot.is_enabled() is True

    def test_set_enabled_false_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", True)
        import agent.iot as iot
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)
        iot.init_db()
        # Prevent dashboard WS broadcast
        with patch("dashboard.server.ws_manager.broadcast_threadsafe"):
            iot.set_enabled(False, source="test")
        assert iot.is_enabled() is False

    def test_set_enabled_true_unblocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", True)
        import agent.iot as iot
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "test.db"))
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)
        iot.init_db()
        with patch("dashboard.server.ws_manager.broadcast_threadsafe"):
            iot.set_enabled(False, source="test")
            iot.set_enabled(True, source="test")
        assert iot.is_enabled() is True


# ---------------------------------------------------------------------------
# HMAC signature verification
# ---------------------------------------------------------------------------

class TestSignatureVerification:
    def test_no_secret_allows_all(self, monkeypatch):
        monkeypatch.setattr("config.IOT_WEBHOOK_SECRET", "")
        import agent.iot as iot
        assert iot.verify_signature(None, b"any body") is True
        assert iot.verify_signature("wrong", b"any body") is True

    def test_missing_signature_rejected(self, monkeypatch):
        monkeypatch.setattr("config.IOT_WEBHOOK_SECRET", "supersecret")
        import agent.iot as iot
        assert iot.verify_signature(None, b"body") is False

    def test_correct_signature_accepted(self, monkeypatch):
        import hmac as _hmac, hashlib
        monkeypatch.setattr("config.IOT_WEBHOOK_SECRET", "supersecret")
        import agent.iot as iot
        body = b'{"entity_id":"binary_sensor.door"}'
        sig = _hmac.new(b"supersecret", body, hashlib.sha256).hexdigest()
        assert iot.verify_signature(sig, body) is True

    def test_wrong_signature_rejected(self, monkeypatch):
        monkeypatch.setattr("config.IOT_WEBHOOK_SECRET", "supersecret")
        import agent.iot as iot
        assert iot.verify_signature("deadbeef", b"body") is False


# ---------------------------------------------------------------------------
# tools/iot.py — dispatch_inbound
# ---------------------------------------------------------------------------

class TestDispatchInbound:
    def _make_payload(self, entity_id="binary_sensor.front_door", event="opened"):
        return {"entity_id": entity_id, "event": event, "state": "on"}

    def test_dispatch_blocked_when_iot_disabled(self, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", False)
        import tools.iot as ch
        called = []
        ch.set_agent_run_fn(lambda t, **kw: called.append(t) or "ok")
        result = ch.dispatch_inbound(self._make_payload())
        assert result is None
        assert not called

    def test_dispatch_blocked_non_allowlisted(self, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", True)
        monkeypatch.setattr("config.IOT_TRIGGER_ALLOWED_ENTITIES", ["binary_sensor.backdoor"])
        with patch("agent.iot.is_enabled", return_value=True):
            import tools.iot as ch
            called = []
            ch.set_agent_run_fn(lambda t, **kw: called.append(t) or "ok")
            result = ch.dispatch_inbound(self._make_payload("binary_sensor.front_door"))
        assert result is None
        assert not called

    def test_dispatch_fires_agent_run(self, monkeypatch):
        monkeypatch.setattr("config.IOT_ENABLED", True)
        monkeypatch.setattr("config.IOT_TRIGGER_ALLOWED_ENTITIES", [])
        with patch("agent.iot.is_enabled", return_value=True):
            import tools.iot as ch
            calls = []
            ch.set_agent_run_fn(lambda t, **kw: calls.append((t, kw)) or "reply")
            result = ch.dispatch_inbound(self._make_payload())
        assert result == "reply"
        assert calls
        text, kw = calls[0]
        assert "binary_sensor.front_door" in text
        assert kw.get("channel_id") == "iot:binary_sensor.front_door"


# ---------------------------------------------------------------------------
# tools/iot.py — iot_call_service disabled guard
# ---------------------------------------------------------------------------

class TestIotCallService:
    def test_returns_disabled_message_when_off(self, monkeypatch):
        with patch("agent.iot.is_enabled", return_value=False):
            import tools.iot as ch
            result = ch.iot_call_service("light", "turn_on", {"entity_id": "light.kitchen"})
        assert "disabled" in result.lower()

    def test_calls_ha_when_enabled(self):
        with patch("agent.iot.is_enabled", return_value=True), \
             patch("agent.iot.ha_call_service", return_value={"ok": True, "result": []}) as mock_ha:
            import tools.iot as ch
            result = ch.iot_call_service("light", "turn_on", {"entity_id": "light.kitchen"})
        assert "successfully" in result.lower()
        mock_ha.assert_called_once_with("light", "turn_on", {"entity_id": "light.kitchen"})


# ---------------------------------------------------------------------------
# Safety gate — IoT HA rules
# ---------------------------------------------------------------------------

class TestIotSafetyRules:
    def setup_method(self):
        from agent import safety
        safety.set_confirm_fn(lambda _: False)  # deny all by default

    def test_lock_domain_blocked(self):
        from agent import safety
        proceed, reason = safety.check("iot_call_service", {"domain": "lock", "service": "unlock", "data": {}})
        assert not proceed
        assert "lock" in reason.lower() or "alarm" in reason.lower()

    def test_alarm_domain_blocked(self):
        from agent import safety
        proceed, reason = safety.check("iot_call_service", {"domain": "alarm_control_panel", "service": "disarm", "data": {}})
        assert not proceed

    def test_light_domain_passes(self):
        from agent import safety
        safety.set_confirm_fn(lambda _: True)
        proceed, _ = safety.check("iot_call_service", {"domain": "light", "service": "turn_on", "data": {}})
        assert proceed

    def test_unlock_service_blocked(self):
        from agent import safety
        proceed, _ = safety.check("iot_call_service", {"domain": "lock", "service": "unlock", "data": {}})
        assert not proceed

    def test_garage_in_data_blocked(self):
        from agent import safety
        proceed, _ = safety.check("iot_call_service", {"domain": "cover", "service": "open_cover", "data": "garage_door"})
        assert not proceed


# ---------------------------------------------------------------------------
# Config flags present
# ---------------------------------------------------------------------------

class TestIotConfig:
    def test_iot_flags_exist(self):
        import config
        assert hasattr(config, "IOT_ENABLED")
        assert hasattr(config, "IOT_HA_URL")
        assert hasattr(config, "IOT_HA_TOKEN")
        assert hasattr(config, "IOT_WEBHOOK_SECRET")
        assert hasattr(config, "IOT_AWARENESS_ENTITIES")
        assert hasattr(config, "IOT_TRIGGER_ALLOWED_ENTITIES")
        assert isinstance(config.IOT_AWARENESS_ENTITIES, list)
        assert isinstance(config.IOT_TRIGGER_ALLOWED_ENTITIES, list)

    def test_iot_disabled_by_default(self):
        import config
        # CI won't have IOT_ENABLED set → should default to False
        import os
        if os.getenv("IOT_ENABLED", "").lower() not in {"1", "true", "yes"}:
            assert config.IOT_ENABLED is False


class TestIotSharesTheOneConnectionPath:
    """`agent/iot.py` used to open its own `sqlite3.connect(_DB_PATH)`, against
    a path resolved from the environment at IMPORT time.

    42 modules go through `longterm._conn()`, which reads `longterm.DB_PATH` at
    CALL time. This one did not follow it, and the divergence was invisible
    because the two paths are normally the same string. Two consequences:

      * The tests above had to monkeypatch `iot._DB_PATH` separately. Any test
        that patched only `longterm.DB_PATH` — the convention everywhere else —
        left IoT writing to the real database.
      * Anything that relocates or snapshots the brain would miss IoT state.
        The subsystem would keep working locally and quietly diverge, which is
        the shape this codebase keeps producing.

    This test is what stops it coming back. It asserts behaviour, not the
    absence of a symbol: re-adding a private path global would not fail here,
    but re-adding one that IoT actually *used* would.
    """

    def test_iot_writes_follow_longterm_db_path(self, tmp_path, monkeypatch):
        import sqlite3
        import agent.iot as iot
        monkeypatch.setattr("config.IOT_ENABLED", True)
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "follow.db"))
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)

        iot.init_db()
        with patch("dashboard.server.ws_manager.broadcast_threadsafe"):
            iot.set_enabled(False, source="test")

        with sqlite3.connect(tmp_path / "follow.db") as c:
            rows = c.execute("SELECT key, value FROM iot_settings").fetchall()
        assert rows, (
            "IoT wrote nothing to the database longterm.DB_PATH points at — it "
            "is using a connection path of its own again")

    def test_pointing_somewhere_new_does_not_carry_state_across(
            self, tmp_path, monkeypatch):
        """The half that proves the path is read per call, not cached. A module
        that resolved the path once at import would pass the test above and
        still write every subsequent call to the first database it saw."""
        import agent.iot as iot
        monkeypatch.setattr("config.IOT_ENABLED", True)
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)

        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "first.db"))
        iot.init_db()
        with patch("dashboard.server.ws_manager.broadcast_threadsafe"):
            iot.set_enabled(False, source="test")

        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "second.db"))
        monkeypatch.setattr(iot, "_cache_value", None)
        monkeypatch.setattr(iot, "_cache_ts", 0.0)
        iot.init_db()
        assert iot.is_enabled() is True, (
            "the second database has no 'disabled' row, so IoT should read as "
            "enabled — reading False means the write went to the first one")
