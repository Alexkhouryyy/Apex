"""The live readout: why a pinch did not grab.

Phase 8's success check is "pinch, move, rotate, scale work reliably", and the
thing standing between it and an answer was never the code — it was that a
failed pinch is silent. Five different causes produce the identical outcome of
"nothing happened":

    the hand is not pinched            ratio did not fall below the threshold
    the pinch has not held long enough  ARM_DWELL_SECONDS
    nothing is within reach             GRAB_RADIUS
    the nearest card is already full    two hands on it
    an open palm is cancelling

Until now the only deciding number, `pinch_ratio`, existed solely inside a
HANDTRACK_DEBUG print into a scrolling terminal — which 7fc8f34 had already
concluded is the wrong place to read a threshold off.
"""
from __future__ import annotations

import pytest

from agent import board as board_mod
from agent.board import Board, GRAB_RADIUS, ARM_DWELL_SECONDS, HandState


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "board.db"))
    longterm.init_db()
    board_mod.init_db()


def placed(b: Board, title: str, x: float, y: float):
    card = b.add("note", title, src="")
    card.x, card.y = x, y
    return card


class TestItNamesTheReasonNothingHappened:

    def test_an_open_hand_is_idle_and_says_the_card_is_in_reach(self, db):
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)])
        r = b.hand_report()[0]
        assert r["state"] == HandState.IDLE
        assert r["pinched"] is False
        assert r["in_reach"] is True, "the hand is on the card; only the pinch is missing"

    def test_a_pinch_out_of_reach_reports_the_distance_and_the_reach(self, db):
        """The one that matters. 'It did not grab' is useless; '0.566 away,
        reach is 0.14' is a fix."""
        b = Board()
        placed(b, "Phone Stand", 0.9, 0.9)
        b.apply_hands([(0.1, 0.1, True, False)], now=100.0)
        b.apply_hands([(0.1, 0.1, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.1)
        r = b.hand_report()[0]
        assert r["pinched"] is True
        assert r["state"] == HandState.IDLE
        assert r["in_reach"] is False
        assert r["nearest"] == "Phone Stand"
        assert r["distance"] > GRAB_RADIUS
        assert r["reach"] == GRAB_RADIUS

    def test_the_dwell_window_is_visible_as_arming(self, db):
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        r = b.hand_report()[0]
        assert r["state"] == HandState.ARMED
        assert r["arming"] is True
        assert r["dwell_needed"] == ARM_DWELL_SECONDS

    def test_a_completed_grab_names_what_is_held(self, db):
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.1)
        r = b.hand_report()[0]
        assert r["state"] == HandState.GRABBED
        assert r["holding"] == "Phone Stand"

    def test_an_open_palm_is_reported_because_it_cancels(self, db):
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True, True)])
        r = b.hand_report()[0]
        assert r["open_palm"] is True
        assert r["state"] == HandState.IDLE

    def test_a_card_held_by_two_hands_is_flagged_as_full(self, db):
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        two = [(0.5, 0.5, True, False), (0.5, 0.5, True, False)]
        b.apply_hands(two, now=100.0)
        b.apply_hands(two, now=100.0 + ARM_DWELL_SECONDS + 0.1)
        third = list(two) + [(0.5, 0.5, True, False)]
        b.apply_hands(third, now=100.0 + ARM_DWELL_SECONDS + 0.2)
        r = b.hand_report()[2]
        assert r["nearest_is_full"] is True

    def test_an_empty_board_says_there_is_nothing_rather_than_out_of_reach(self, db):
        b = Board()
        b.apply_hands([(0.5, 0.5, True, False)])
        r = b.hand_report()[0]
        assert r["nearest"] is None and r["distance"] is None


class TestTheReportMatchesTheRuleItDescribes:
    """A readout that quoted a different reach from the one `_nearest` applies
    would send someone tuning the wrong number."""

    def test_the_reach_shown_is_the_reach_used(self, db):
        b = Board()
        placed(b, "Just Inside", 0.5 + GRAB_RADIUS * 0.9, 0.5)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0 + ARM_DWELL_SECONDS + 0.1)
        assert b.hand_report()[0]["state"] == HandState.GRABBED, "inside reach grabs"

        b2 = Board()
        placed(b2, "Just Outside", 0.5 + GRAB_RADIUS * 1.1, 0.5)
        b2.apply_hands([(0.5, 0.5, True, False)], now=200.0)
        b2.apply_hands([(0.5, 0.5, True, False)], now=200.0 + ARM_DWELL_SECONDS + 0.1)
        r = b2.hand_report()[0]
        assert r["state"] == HandState.IDLE and r["in_reach"] is False

    def test_it_reports_the_hands_the_board_acted_on(self, db):
        """Recorded after read_cursors normalises them, so the readout explains
        the same hands the grab logic saw."""
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True, False)])
        assert len(b.hand_report()) == 1
        b.apply_hands([])
        assert b.hand_report() == []

    def test_it_changes_nothing(self, db):
        """Read-only: calling it must not arm, grab or release anything."""
        b = Board()
        placed(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True, False)], now=100.0)
        before = [(c.id, c.x, c.y, list(c.held_by)) for c in b._cards]
        for _ in range(5):
            b.hand_report()
        after = [(c.id, c.x, c.y, list(c.held_by)) for c in b._cards]
        assert before == after


class TestTheTrackerKeepsTheDecidingNumber:

    def test_latest_hands_carries_the_ratio_and_the_threshold(self):
        """`pinched` alone cannot be acted on — the ratio and the threshold it
        was compared against are what tell you which way to tune."""
        from agent.handtrack import HandTracker
        t = HandTracker.__new__(HandTracker)
        import threading
        t._lock = threading.Lock()
        t._latest_hands = [{"label": "Right", "x": 0.5, "y": 0.5, "ratio": 0.83,
                            "threshold": 0.70, "pinched": False, "open_palm": False}]
        got = t.latest_hands()[0]
        assert got["ratio"] == 0.83 and got["threshold"] == 0.70

    def test_it_hands_back_a_copy(self):
        """The caller is a websocket loop; a shared dict would let it mutate
        what the tracker reports next frame."""
        from agent.handtrack import HandTracker
        import threading
        t = HandTracker.__new__(HandTracker)
        t._lock = threading.Lock()
        t._latest_hands = [{"label": "Right", "ratio": 0.4}]
        out = t.latest_hands()
        out[0]["ratio"] = 999
        assert t.latest_hands()[0]["ratio"] == 0.4
