"""Gestures that fired while the hands were working the board.

Found on review, every one reproduced against the real code before it was
fixed. The recognizer knows nothing about cards, so moving a held card IS a
swipe to it and holding one still IS a pinch_hold. Each of these reached an
action the user never asked for — cutting Apex off, opening the mic, paging
the board.
"""
from __future__ import annotations

import inspect
import threading

import pytest

import config
import main
from agent import board as board_mod
from agent import gestures, handtrack
from agent.awareness import AwarenessLog
from agent.board import ARM_DWELL_SECONDS, Board

FRAME = 0.05


def feed(rec, path, t, *, pinched=False):
    """One hand along `path`; returns (gestures fired, time reached)."""
    fired = []
    for x, y in path:
        fired += rec.feed_cursors([(x, y, pinched, False)], t)
        t += FRAME
    return fired, t


def still(rec, x, y, t, seconds, *, pinched=False):
    n = int(seconds / FRAME)
    return feed(rec, [(x, y)] * n, t, pinched=pinched)


class TestTheRecognizer:

    def test_letting_go_after_a_downward_drag_is_not_swipe_down(self):
        """Swipe-down is mapped to stop. Letting go of a card dragged down
        fired it, and Apex stopped talking."""
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        _f, t = still(rec, 0.5, 0.2, 100.0, 1.0)
        _f, t = still(rec, 0.5, 0.2, t, 0.2, pinched=True)
        _f, t = feed(rec, [(0.5, 0.2 + 0.1 * k) for k in range(1, 7)], t, pinched=True)
        fired, t = feed(rec, [(0.5, 0.8), (0.5, 0.82), (0.5, 0.84)], t)   # let go
        assert "swipe_down" not in fired

    def test_a_held_pinch_is_one_pinch_hold_not_one_every_cooldown(self):
        rec = gestures.GestureRecognizer(cooldown_seconds=3.0)
        _f, t = still(rec, 0.5, 0.5, 100.0, 0.5)
        fired, t = still(rec, 0.5, 0.5, t, 10.0, pinched=True)
        assert fired.count("pinch_hold") == 1

    def test_a_new_pinch_can_hold_again(self):
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        _f, t = still(rec, 0.5, 0.5, 100.0, 0.5)
        a, t = still(rec, 0.5, 0.5, t, 1.5, pinched=True)
        _f, t = still(rec, 0.5, 0.5, t, 0.3)
        b, t = still(rec, 0.5, 0.5, t, 1.5, pinched=True)
        assert a.count("pinch_hold") == 1 and b.count("pinch_hold") == 1

    def test_raising_a_hand_into_view_is_not_swipe_up(self):
        """Swipe-up summons Apex. Lifting a hand into the frame is a long fast
        vertical stroke, and it summoned Apex every time."""
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        fired, t = feed(rec, [(0.5, 0.95 - 0.08 * k) for k in range(8)], 100.0)
        assert "swipe_up" not in fired

    def test_the_return_stroke_does_not_undo_a_swipe(self):
        """swipe_right pages forward; bringing the hand back is a swipe_left,
        and with a per-name cooldown it paged straight back."""
        rec = gestures.GestureRecognizer(cooldown_seconds=3.0)
        _f, t = still(rec, 0.2, 0.5, 100.0, 1.0)
        right, t = feed(rec, [(0.2 + 0.1 * k, 0.5) for k in range(1, 7)], t)
        back, t = feed(rec, [(0.8 - 0.1 * k, 0.5) for k in range(1, 7)], t)
        rest, t = still(rec, 0.2, 0.5, t, 0.3)       # hand back where it began
        assert "swipe_right" in right
        assert "swipe_left" not in back + rest

    def test_a_deliberate_swipe_still_works(self):
        rec = gestures.GestureRecognizer(cooldown_seconds=3.0)
        _f, t = still(rec, 0.2, 0.5, 100.0, 1.0)
        fired, t = feed(rec, [(0.2 + 0.1 * k, 0.5) for k in range(1, 7)], t)
        assert "swipe_right" in fired

    def test_refund_gives_the_cooldown_back(self):
        rec = gestures.GestureRecognizer(cooldown_seconds=3.0)
        assert rec._emit(["swipe_left"], 100.0) == ["swipe_left"]
        assert rec._emit(["swipe_left"], 101.0) == []
        rec.refund("swipe_left")
        assert rec._emit(["swipe_left"], 101.0) == ["swipe_left"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "t.db"))
    longterm.init_db()


@pytest.fixture
def tracker_on_board(db, monkeypatch):
    """A real tracker's dispatch, a real board, and a handler that records."""
    b = Board()
    monkeypatch.setattr(board_mod, "get_board", lambda: b)
    monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HANDTRACK_GESTURE_ACTIONS",
                        ["pinch_hold:listen", "swipe_down:stop",
                         "swipe_left:board:prev"], raising=False)
    t = handtrack.HandTracker(AwarenessLog())
    calls = []
    t.on_gesture = lambda g, a: calls.append((g, a))
    return t, b, calls


class TestTheBoardVetoes:

    def test_holding_a_card_still_does_not_open_the_mic(self, tracker_on_board, monkeypatch):
        t, b, calls = tracker_on_board
        c = b.add("card", "Held")
        c.x, c.y = 0.5, 0.5
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.01)
        assert c.held_by
        t._dispatch("pinch_hold")
        assert calls == []

    def test_pinch_hold_in_empty_space_still_listens(self, tracker_on_board):
        t, b, calls = tracker_on_board
        t._dispatch("pinch_hold")
        assert calls == [("pinch_hold", "listen")]

    def test_letting_go_of_a_card_does_not_stop_apex(self, tracker_on_board, monkeypatch):
        t, b, calls = tracker_on_board
        c = b.add("card", "Held")
        c.x, c.y = 0.5, 0.5
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.01)
        b.apply_hands([(0.5, 0.7, False, False)], now=100.3)          # let go
        monkeypatch.setattr(board_mod.time, "time", lambda: 100.4)
        t._dispatch("swipe_down")
        assert calls == []

    def test_a_vetoed_gesture_refunds_its_cooldown(self, tracker_on_board, monkeypatch):
        """Refused swipes spent the 3 s cooldown, so the next real swipe in
        that direction was dropped with no log line."""
        t, b, calls = tracker_on_board
        c = b.add("card", "Held")
        c.x, c.y = 0.5, 0.5
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.01)
        assert t.recognizer._emit(["swipe_down"], 100.2) == ["swipe_down"]
        t._dispatch("swipe_down")                                      # vetoed
        assert t.recognizer._emit(["swipe_down"], 100.5) == ["swipe_down"]

    def test_a_board_action_the_board_refuses_is_refunded_too(self, tracker_on_board, monkeypatch):
        """Not every board action comes from a swipe. wave -> board:next is
        refused inside run_board_action while a hand is pinched, and that
        refusal must give the cooldown back as well."""
        t, b, calls = tracker_on_board
        monkeypatch.setattr(config, "HANDTRACK_GESTURE_ACTIONS",
                            ["wave:board:next"], raising=False)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)   # pinched, holding nothing
        assert t.recognizer._emit(["wave"], 100.1) == ["wave"]
        t._dispatch("wave")
        assert t.recognizer._emit(["wave"], 100.2) == ["wave"]


class TestTheBoardsVoiceHearsGestures:
    """/board's Voice (Celine in the partner panel) is driven by the same
    gestures as the main voice loop: swipe down hushes her, pinch-and-hold
    starts her. Both still reach the main loop's handler as before."""

    def _events(self, b):
        return [e["type"] for e in b.events_since(0)]

    def test_swipe_down_hushes_the_board_and_still_stops_the_main_loop(self, tracker_on_board):
        t, b, calls = tracker_on_board
        t._dispatch("swipe_down")
        assert "hush" in self._events(b)
        assert calls == [("swipe_down", "stop")]

    def test_pinch_hold_starts_listening_on_the_board(self, tracker_on_board):
        t, b, calls = tracker_on_board
        t._dispatch("pinch_hold")
        assert "listen" in self._events(b)
        assert calls == [("pinch_hold", "listen")]

    def test_a_vetoed_gesture_tells_the_board_nothing(self, tracker_on_board):
        t, b, calls = tracker_on_board
        c = b.add("card", "Held")
        c.x, c.y = 0.5, 0.5
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.01)
        t._dispatch("pinch_hold")                    # holding a card still
        assert "listen" not in self._events(b)

    def test_board_off_sends_nothing(self, tracker_on_board, monkeypatch):
        t, b, calls = tracker_on_board
        monkeypatch.setattr(config, "BOARD_ENABLED", False, raising=False)
        t._dispatch("swipe_down")
        assert "hush" not in self._events(b)


class TestCelineCanWorkTheBoardByVoice:
    def test_reversible_board_tools_are_allowed_in_discuss(self):
        from agent import companion
        for tool in ("board_present", "board_model", "board_transform", "board_undo", "board_redo", "board_build"):
            assert tool in companion.DISCUSS_TOOLS
        # One misheard sentence must not empty the board.
        assert "board_clear" not in companion.DISCUSS_TOOLS


class TestStopMeansTheReply:

    class _Streamer:
        def __init__(self):
            self.interrupted = False

        def interrupt(self):
            self.interrupted = True

    def test_swipe_down_between_sentences_stops_the_rest(self, monkeypatch):
        """Nothing is playing while a tool runs, so "is audio playing?" said
        nothing to stop — and the reply carried on."""
        from voice import tts
        monkeypatch.setattr(tts, "is_speaking", lambda: False)
        reply = main.ActiveReply()
        streamer, cancel = self._Streamer(), threading.Event()
        reply.begin(streamer, cancel)
        out = main.make_gesture_handler("voice", reply=reply)("swipe_down", "stop")
        assert cancel.is_set() and streamer.interrupted
        assert out == "swipe_down: stopped the reply"

    def test_no_reply_and_no_sound_is_nothing_to_stop(self, monkeypatch):
        from voice import tts
        monkeypatch.setattr(tts, "is_speaking", lambda: False)
        out = main.make_gesture_handler("voice", reply=main.ActiveReply())("swipe_down", "stop")
        assert out == "swipe_down: nothing to stop"

    def test_the_voice_loop_registers_its_reply(self):
        src = inspect.getsource(main.main)
        assert "ACTIVE_REPLY.begin(streamer, cancel)" in src
        assert "cancel_event=cancel" in src
        assert "ACTIVE_REPLY.end()" in src


def test_tui_mode_gets_the_gesture_handler():
    """--tui is what Apex.bat launches, and main() returns inside the TUI
    branch. Wiring placed after it never ran there."""
    src = inspect.getsource(main.main)
    assert src.index("_tracker.on_gesture = make_gesture_handler(") < src.index("run_tui(agent)")
