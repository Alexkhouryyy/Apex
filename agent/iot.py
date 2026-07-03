"""IoT integration state — kill switch, settings table, HA REST helpers.

All three IoT layers (MCP control, awareness watcher, inbound webhook) call
is_enabled() before acting. The runtime flag is stored in SQLite so it survives
restarts and can be toggled without restarting the agent.
"""
import hashlib
import hmac
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Optional

import config

# ---------------------------------------------------------------------------
# DB helpers (reuses the same SQLite DB as longterm.py)
# ---------------------------------------------------------------------------
import os
_DB_PATH = os.path.expanduser(os.getenv("DB_PATH", "~/.voice_agent_memory.db"))

_cache_lock = threading.Lock()
_cache_value: Optional[bool] = None
_cache_ts: float = 0.0
_CACHE_TTL = 5.0  # seconds


@contextmanager
def _db():
    # timeout + busy_timeout: this writer shares the DB file with the main loop
    # and background threads; wait for the lock instead of raising immediately.
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS iot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)


def _db_get_enabled() -> bool:
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT value FROM iot_settings WHERE key='enabled'"
            ).fetchone()
            if row is None:
                return True  # default on when env flag is set
            return row[0] == "1"
    except Exception:
        return True


def is_enabled() -> bool:
    """Both env flag AND runtime flag must be True for IoT to act."""
    if not config.IOT_ENABLED:
        return False
    global _cache_value, _cache_ts
    with _cache_lock:
        if _cache_value is None or time.time() - _cache_ts > _CACHE_TTL:
            _cache_value = _db_get_enabled()
            _cache_ts = time.time()
        return _cache_value


def set_enabled(value: bool, *, source: str = "api") -> None:
    global _cache_value, _cache_ts
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO iot_settings(key, value, updated_at) VALUES('enabled',?,?)",
            ("1" if value else "0", time.time()),
        )
    with _cache_lock:
        _cache_value = value
        _cache_ts = time.time()

    # Broadcast WS event so the dashboard updates live
    try:
        from dashboard import server as _dash
        _dash.ws_manager.broadcast_threadsafe({
            "type": "iot_toggle",
            "enabled": value,
            "source": source,
            "ts": time.time(),
        })
    except Exception:
        pass

    print(f"[IoT] {'Enabled' if value else 'Disabled'} (source: {source})")


# ---------------------------------------------------------------------------
# HMAC verification for inbound webhooks
# ---------------------------------------------------------------------------

def verify_signature(signature: Optional[str], body: bytes) -> bool:
    """Returns True if signature matches HMAC-SHA256(body, IOT_WEBHOOK_SECRET)."""
    secret = config.IOT_WEBHOOK_SECRET
    if not secret:
        return True  # no secret configured → open (warn at startup)
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# Home Assistant REST helpers
# ---------------------------------------------------------------------------

def _ha_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.IOT_HA_TOKEN}",
        "Content-Type": "application/json",
    }


def ha_get_state(entity_id: str) -> dict:
    """GET /api/states/{entity_id} — returns state dict or error dict."""
    if not config.IOT_HA_URL or not config.IOT_HA_TOKEN:
        return {"error": "IOT_HA_URL or IOT_HA_TOKEN not configured"}
    url = f"{config.IOT_HA_URL.rstrip('/')}/api/states/{entity_id}"
    try:
        req = urllib.request.Request(url, headers=_ha_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def ha_call_service(domain: str, service: str, data: dict) -> dict:
    """POST /api/services/{domain}/{service} — returns result or error dict."""
    if not config.IOT_HA_URL or not config.IOT_HA_TOKEN:
        return {"error": "IOT_HA_URL or IOT_HA_TOKEN not configured"}
    url = f"{config.IOT_HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    payload = json.dumps(data).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers=_ha_headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "result": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def ha_notify(target: str, message: str, title: str = "") -> dict:
    """Call notify.{target} service on HA."""
    data: dict = {"message": message}
    if title:
        data["title"] = title
    return ha_call_service("notify", target, data)
