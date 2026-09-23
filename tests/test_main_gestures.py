"""wave / pinch-hold / swipe-down in `main.py`.

Only app/resident.py used to set the tracker's on_gesture hook, so in text,
voice and wake mode these gestures were recognised, logged, and then did
nothing without saying so. make_gesture_handler gives every mode an answer.
"""
from __future__ import annotations

import inspect
import threading

import pytest

import config
import main
from voice import interrupt, tts


class _Log:
    def __init__(self):
        self.events = []

    def add(self, source, content):
        self.events.append((source, content))


@pytest.fixture(autouse=True)
def _clear_interrupt():
    interrupt.reset()
    yield
    interrupt.reset()


class TestWake:
    def test_wave_wakes_apex_in_wake_mode(self):
        ev = threading.Event()
        h = main.make_gesture_handler("wake", wake_event=ev)
        assert h("wave", "wake") == "wave: listening"
        assert ev.is_set()

    def test_pinch_hold_listen_also_wakes(self):
        ev = threading.Event()
        main.make_gesture_handler("wake", wake_event=ev)("pinch_hold", "listen")
        assert ev.is_set()

    @pytest.mark.parametrize("mode", ["text", "tui"])
    def test_text_modes_say_why_nothing_happened(self, mode):
        ev = threading.Event()
        log = _Log()
        out = main.make_gesture_handler(mode, wake_event=ev, log=log)("wave", "wake")
        assert not ev.is_set()
        assert "no microphone" in out
        assert log.events == [("gesture", out)]

    def test_voice_mode_is_already_listening(self):
        ev = threading.Event()
        out = main.make_gesture_handler("voice", wake_event=ev)("wave", "wake")
        assert "already listening" in out and not ev.is_set()


class TestStop:
    def test_swipe_down_cuts_voicebox_mid_sentence(self, monkeypatch):
        monkeypatch.setattr(tts, "is_speaking", lambda: True)
        monkeypatch.setattr(config, "TTS_ENGINE", "voicebox", raising=False)
        out = main.make_gesture_handler("voice")("swipe_down", "stop")
        assert interrupt.is_interrupted()
        assert out == "swipe_down: stopped speaking"

    def test_other_engines_are_not_claimed_to_stop_instantly(self, monkeypatch):
        monkeypatch.setattr(tts, "is_speaking", lambda: True)
        monkeypatch.setattr(config, "TTS_ENGINE", "pyttsx3", raising=False)
        out = main.make_gesture_handler("voice")("swipe_down", "stop")
        assert interrupt.is_interrupted()
        assert "finishes the current sentence" in out

    def test_nothing_to_stop_leaves_no_stale_interrupt(self, monkeypatch):
        """A stray swipe while silent must not pre-cut the NEXT reply —
        speak() resets the flag, but only this check keeps the log honest."""
        monkeypatch.setattr(tts, "is_speaking", lambda: False)
        out = main.make_gesture_handler("voice")("swipe_down", "stop")
        assert out == "swipe_down: nothing to stop"
        assert not interrupt.is_interrupted()


def test_unknown_action_is_reported():
    log = _Log()
    out = main.make_gesture_handler("text", log=log)("wave", "dance")
    assert "unknown action 'dance'" in out and log.events


def test_main_wires_the_handler_but_never_over_resident():
    """The hook is installed in main() and only when nothing else set it —
    resident.py's own handler must win."""
    src = inspect.getsource(main.main)
    assert "make_gesture_handler(" in src
    assert "_tracker.on_gesture is None" in src
