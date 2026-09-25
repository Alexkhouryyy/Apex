"""Tap to ask: a quick pinch on an object, let go without moving it.

A tap has to be told apart from everything else a pinch does, and each of
those must stay what it was:
- a grab that MOVES the object is a move (undoable), not a tap;
- a pinch HELD still is a hold, not a tap;
- a flick is a throw; an open palm is a cancel; two hands are a transform.

What would be wrong if these failed: Celine answering questions nobody asked
every time you put something down, or a tap nudging the object and filling
undo with steps that changed nothing.
"""
import pytest

from agent.board import ARM_DWELL_SECONDS, Board, TAP_MOVE, TAP_SECONDS

COMMIT = ARM_DWELL_SECONDS + 0.01


@pytest.fixture
def board():
    b = Board()
    c = b.add("model", "Rocket", src="created/rocket/v1.glb", x=0.5, y=0.5)
    return b, c


def taps(b):
    return [e for e in b.events_since(0) if e["type"] == "tapped"]


def pinch(b, frames):
    """frames: [(t, x, y, pinched, open_palm)] for hand 0."""
    for t, x, y, p, o in frames:
        b.apply_hands([(x, y, p, o, 0)], now=t)


class TestATap:
    def test_a_quick_still_pinch_is_a_tap(self, board):
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (COMMIT, .51, .5, True, False),
                  (COMMIT + .1, .51, .5, True, False), (COMMIT + .2, .51, .5, False, False)])
        ev = taps(b)
        assert len(ev) == 1 and ev[0]["id"] == c.id and ev[0]["title"] == "Rocket"
        assert ev[0]["object_kind"] == "model"

    def test_a_tap_does_not_nudge_it_or_spend_an_undo(self, board):
        b, c = board
        steps = len(b._undo)                       # putting it on the board
        pinch(b, [(0, .52, .5, True, False), (COMMIT, .52, .5, True, False),
                  (COMMIT + .05, .53, .51, True, False), (COMMIT + .1, .53, .51, False, False)])
        assert taps(b)
        assert (c.x, c.y) == (.5, .5), "the tap moved it"
        assert len(b._undo) == steps, "a tap must leave nothing for undo"

    def test_a_tap_makes_it_this(self, board):
        b, c = board
        pinch(b, [(10, .5, .5, True, False), (10 + COMMIT, .5, .5, True, False), (10.3, .5, .5, False, False)])
        pointed = b.pointed(now=10.5)
        assert pointed and pointed["id"] == c.id
        assert b.selection()["id"] == c.id


class TestNotATap:
    def test_a_move_is_a_move(self, board):
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (COMMIT, .5, .5, True, False),
                  (COMMIT + .1, .5 + TAP_MOVE * 2, .5, True, False),
                  (COMMIT + .2, .5 + TAP_MOVE * 2, .5, False, False)])
        assert taps(b) == []
        assert c.x != .5
        assert b._undo[-1]["kind"] == "transform", "a move must stay undoable"

    def test_moving_away_and_back_is_still_a_move(self, board):
        # Judged on the furthest the hand went, not where it ended.
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (COMMIT, .5, .5, True, False),
                  (COMMIT + .1, .6, .5, True, False), (COMMIT + .2, .5, .5, True, False),
                  (COMMIT + .25, .5, .5, False, False)])
        assert taps(b) == []

    def test_holding_it_still_is_a_hold(self, board):
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (COMMIT, .5, .5, True, False),
                  (COMMIT + TAP_SECONDS + .1, .5, .5, True, False),
                  (COMMIT + TAP_SECONDS + .2, .5, .5, False, False)])
        assert taps(b) == []

    def test_an_open_palm_cancel_is_not_a_tap(self, board):
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (COMMIT, .5, .5, True, False),
                  (COMMIT + .1, .5, .5, False, True)])
        assert taps(b) == []

    def test_a_pinch_too_short_to_grab_does_nothing(self, board):
        b, c = board
        pinch(b, [(0, .5, .5, True, False), (0.05, .5, .5, False, False)])
        assert taps(b) == [] and not c.held_by

    def test_two_hands_are_a_transform_not_a_tap(self, board):
        b, c = board
        for t in (0, COMMIT):
            b.apply_hands([(.49, .5, True, False, 0), (.51, .5, True, False, 1)], now=t)
        b.apply_hands([(.49, .5, False, False, 0), (.51, .5, False, False, 1)], now=COMMIT + .1)
        assert taps(b) == []

    def test_a_throw_is_a_throw(self, board):
        b, c = board
        frames = [(0, .5, .5, True, False), (COMMIT, .5, .5, True, False)]
        t, x = COMMIT, .5
        for _ in range(4):
            t += .03; x += .12
            frames.append((t, x, .5, True, False))
        frames.append((t + .03, x + .12, .5, False, False))
        pinch(b, frames)
        assert taps(b) == []
        assert all(card["id"] != c.id for card in b.cards()), "it should have been thrown"
