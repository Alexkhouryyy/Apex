"""Unit tests for the terminal UI: streamer + turn cancellation.

Covers tui/streamer.py and the cancel-event plumbing in agent/core.py
(_stream_turn interruption and run() honoring a pre-set cancel event).
"""
import threading
import pytest

import config
from tui.streamer import TUIStreamer


class TestTUIStreamer:
    def test_feed_accumulates_text(self):
        s = TUIStreamer(threading.Event())
        s.feed("hel")
        s.feed("lo")
        assert s.text == "hello"

    def test_interrupt_sets_event(self):
        ev = threading.Event()
        s = TUIStreamer(ev)
        assert not ev.is_set()
        s.interrupt()
        assert ev.is_set()

    def test_start_and_finish_do_not_raise(self):
        s = TUIStreamer(threading.Event())
        s.start()
        s.finish()


# --- Fakes for the streaming Anthropic client -------------------------------

class _FakeStream:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        D = type("D", (), {"type": "text_delta", "text": "hi "})
        E = type("E", (), {"type": "content_block_delta", "delta": D()})
        for _ in range(3):
            yield E()

    def get_final_message(self):
        TB = type("TB", (), {"type": "text", "text": "done"})
        return type("M", (), {"content": [TB()], "stop_reason": "end_turn", "usage": None})()


class _FakeClient:
    class messages:
        @staticmethod
        def stream(**kw):
            return _FakeStream()


class _Cap:
    """Minimal streamer that records fed deltas."""
    def __init__(self):
        self.fed = []

    def start(self):
        pass

    def feed(self, t):
        self.fed.append(t)

    def finish(self):
        pass


def _agent(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key", raising=False)
    from agent.core import AgentCore
    a = AgentCore()
    a.client = _FakeClient()
    return a


class TestStreamTurnCancellation:
    def test_cancel_before_stream_returns_interrupted(self, monkeypatch):
        monkeypatch.setattr(_FakeClient.messages, "stream", lambda **kw: pytest.fail("cancelled request was sent"))
        a = _agent(monkeypatch)
        cancel = threading.Event()
        cancel.set()
        cap = _Cap()
        content, stop, text = a._stream_turn({}, cap, cancel)
        assert stop == "end_turn"
        assert content[0]["text"] == "[INTERRUPTED]"
        assert cap.fed == []

    def test_no_cancel_completes_normally(self, monkeypatch, test_db):
        a = _agent(monkeypatch)
        cap = _Cap()
        content, stop, text = a._stream_turn({"model": "claude-opus-5", "max_tokens": 100}, cap, threading.Event())
        assert stop == "end_turn"
        assert "".join(cap.fed) == "hi hi hi "
        assert text == "hi hi hi"


class TestRunHonorsCancel:
    def test_preset_cancel_returns_without_calling_api(self, monkeypatch, test_db):
        from agent import goals, telemetry
        goals.init_db()
        a = _agent(monkeypatch)

        def fake_create(*args, **kwargs):
            raise AssertionError("API must not be called when pre-cancelled")

        monkeypatch.setattr(telemetry, "create", fake_create)

        cancel = threading.Event()
        cancel.set()
        out = a.run("hello", include_screenshot=False, cancel_event=cancel)
        assert isinstance(out, str)
