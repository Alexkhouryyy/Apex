"""IoT awareness watcher — subscribes to Home Assistant WebSocket state_changed events.

Events that match the IOT_AWARENESS_ENTITIES allowlist are pushed into the
AwarenessLog ring buffer. The existing 60 s AwarenessMonitor reviewer then
decides whether to speak up — no new review logic needed.

WebSocket protocol: https://developers.home-assistant.io/docs/api/websocket
"""
import json
import threading
import time
from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from agent.awareness import AwarenessLog


def decide_event(msg: dict) -> Optional[str]:
    """Should this Home Assistant message reach the awareness log, and as what?

    Module-level and pure so it can be tested without a Home Assistant instance.
    It used to sit inside _connect_and_listen's websocket loop, unreachable by
    anything — the same shape as the wake matcher buried in voice/wake.py's
    audio loop.

    Returns the line to log, or None to drop. Four reasons to drop, in order:
    not an event, not on the allowlist, the kill switch is off, or the state did
    not actually change (Home Assistant re-emits unchanged states, and logging
    those would bury the real ones).
    """
    if not isinstance(msg, dict) or msg.get("type") != "event":
        return None

    data = (msg.get("event") or {}).get("data") or {}
    entity_id = data.get("entity_id") or ""
    new_state = data.get("new_state") or {}
    old_state = data.get("old_state") or {}

    allowlist = config.IOT_AWARENESS_ENTITIES
    if allowlist and entity_id not in allowlist:
        return None

    # Runtime kill switch, checked per event so it takes effect immediately
    # rather than at the next reconnect.
    from agent.iot import is_enabled
    if not is_enabled():
        return None

    old_val = old_state.get("state", "?")
    new_val = new_state.get("state", "?")
    if old_val == new_val:
        return None

    return f"{entity_id}: {old_val} → {new_val}"


class IoTWatcher(threading.Thread):
    """Daemon thread that maintains a HA WebSocket connection and feeds events
    into the shared AwarenessLog."""

    def __init__(self, log: "AwarenessLog", reconnect_delay: float = 5.0):
        super().__init__(daemon=True, name="IoTWatcher")
        self.log = log
        self.reconnect_delay = reconnect_delay
        self._stop = threading.Event()
        self._ws = None

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def run(self) -> None:
        delay = self.reconnect_delay
        while not self._stop.is_set():
            try:
                self._connect_and_listen()
                delay = self.reconnect_delay  # reset on clean disconnect
            except Exception as e:
                if self._stop.is_set():
                    break
                print(f"[IoTWatcher] Connection error: {e}. Reconnecting in {delay}s…")
                self._stop.wait(timeout=delay)
                delay = min(delay * 2, 60)

    def _connect_and_listen(self) -> None:
        ha_url = (config.IOT_HA_URL or "").rstrip("/")
        if not ha_url or not config.IOT_HA_TOKEN:
            print("[IoTWatcher] IOT_HA_URL or IOT_HA_TOKEN not set — watcher idle.")
            self._stop.wait()
            return

        ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"

        try:
            import websocket  # type: ignore
        except ImportError:
            print("[IoTWatcher] websocket-client not installed. Run: pip install websocket-client")
            self._stop.wait()
            return

        ws = websocket.WebSocket()
        ws.connect(ws_url, timeout=15)
        self._ws = ws

        msg_id = 1

        def _send(payload: dict) -> None:
            nonlocal msg_id
            payload["id"] = msg_id
            msg_id += 1
            ws.send(json.dumps(payload))

        # HA WS handshake: receive auth_required → send auth → receive auth_ok
        auth_req = json.loads(ws.recv())
        if auth_req.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected HA WS handshake: {auth_req}")

        ws.send(json.dumps({"type": "auth", "access_token": config.IOT_HA_TOKEN}))
        auth_resp = json.loads(ws.recv())
        if auth_resp.get("type") == "auth_invalid":
            raise RuntimeError("HA WebSocket auth failed — check IOT_HA_TOKEN")
        if auth_resp.get("type") != "auth_ok":
            raise RuntimeError(f"Unexpected auth response: {auth_resp}")

        # Subscribe to state_changed events
        _send({"type": "subscribe_events", "event_type": "state_changed"})
        sub_resp = json.loads(ws.recv())
        if not sub_resp.get("success"):
            raise RuntimeError(f"subscribe_events failed: {sub_resp}")

        print(f"[IoTWatcher] Connected to {ws_url}, watching {len(config.IOT_AWARENESS_ENTITIES) or 'all'} entities.")

        while not self._stop.is_set():
            try:
                raw = ws.recv()
            except Exception:
                if self._stop.is_set():
                    return
                raise

            if not raw:
                continue

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            line = decide_event(msg)
            if line is None:
                continue
            self.log.add("iot", line)

        ws.close()
        self._ws = None
