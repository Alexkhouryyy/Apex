"""Unit tests for tools/telegram.py — inbound dispatch and long-polling.

No network calls: urllib.request.urlopen is monkeypatched so getUpdates /
sendMessage are simulated entirely in-process.
"""
import json
import threading

import pytest

import config
from tools import telegram


@pytest.fixture(autouse=True)
def reset_telegram(monkeypatch):
    """Reset module + config state so tests don't leak into each other."""
    monkeypatch.setattr(telegram, "_agent_run_fn", None, raising=False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", [], raising=False)
    yield


class _FakeResp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _update(text="hi", chat_id=123, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"username": "alex"},
            "text": text,
        },
    }


def test_dispatch_inbound_runs_agent_and_replies(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        telegram, "_agent_run_fn",
        lambda text, *, channel_id=None: seen.update(text=text, channel_id=channel_id) or "pong",
        raising=False,
    )
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda cid, txt: sent.append((cid, txt)))
    # Deny-by-default: the sender must be on the allowlist to reach the agent.
    monkeypatch.setattr(telegram, "_allowed_ids", lambda: {555})

    reply = telegram.dispatch_inbound(_update("ping", chat_id=555))

    assert reply == "pong"
    assert seen["text"] == "[Telegram from @alex] ping"
    assert seen["channel_id"] == "telegram:555"
    assert sent == [(555, "pong")]


def test_dispatch_inbound_blocks_unlisted_chat(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", ["999"], raising=False)
    monkeypatch.setattr(
        telegram, "_agent_run_fn",
        lambda *a, **k: pytest.fail("agent must not run for unauthorized chat"),
        raising=False,
    )
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda cid, txt: sent.append((cid, txt)))

    assert telegram.dispatch_inbound(_update(chat_id=123)) is None
    assert sent and "not authorized" in sent[0][1].lower()


def test_poll_loop_dispatches_one_update(monkeypatch):
    """_poll_loop should pull an update via getUpdates and dispatch it, then
    stop cleanly when the stop event is set."""
    dispatched = []
    monkeypatch.setattr(telegram, "dispatch_inbound", lambda u: dispatched.append(u))

    stop = threading.Event()
    calls = {"getUpdates": 0}

    def fake_urlopen(url, *a, **k):
        if "getUpdates" in url:
            calls["getUpdates"] += 1
            if calls["getUpdates"] == 1:
                return _FakeResp({"ok": True, "result": [_update("hello", update_id=7)]})
            stop.set()  # second poll: signal exit
            return _FakeResp({"ok": True, "result": []})
        return _FakeResp({"ok": True})  # deleteWebhook / anything else

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)

    telegram._poll_loop(stop)

    assert len(dispatched) == 1
    assert dispatched[0]["update_id"] == 7


def test_start_polling_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "", raising=False)
    assert "not configured" in telegram.start_polling().lower()
