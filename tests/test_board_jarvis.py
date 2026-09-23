"""Point, flick, swipe, summon — the board as something you work with, not on.

Four interactions, each with the failure mode that would make it worse than not
having it:

  * POINT AND SAY "THIS"  — "this" has to mean the card your hand was on when
    you spoke, and it has to survive the seconds between pointing and the
    sentence arriving. Forget too fast and "this" means nothing; remember too
    long and it means something you pointed at a minute ago.
  * FLICK TO THROW AWAY   — the dangerous one. A throw deletes a card, so an
    ordinary set-down must never be read as one, and every throw must be
    undoable back to where the card came FROM, not where it was flung to.
  * SWIPES                — the recognizer reads motion and knows nothing about
    cards, so dragging a card IS a swipe to it. A swipe that also fired while
    you were moving a card would step the selection out from under you.
  * SUMMON AT YOUR HAND   — "show me my calendar" lands where you are reaching,
    and falls back to the usual spot when no hand has been up recently.

Every sequence passes real timestamps. The first version of the flick check
trusted speeds from frames microseconds apart and threw away a card that had
just been set down — found by an existing drag test that passes no `now`.
"""
from __future__ import annotations

import pytest

from agent import board as board_mod
from agent.board import (Board, ARM_DWELL_SECONDS, CARD_W, FLICK_SPEED,
                         POINT_MEMORY_SECONDS, is_flick, flick_velocity)

FRAME = 0.05                                    # 20 Hz, the tracker's rate


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "b.db"))
    longterm.init_db()
    board_mod.init_db()


def card_at(b: Board, title: str, x: float, y: float):
    c = b.add("card", title)
    c.x, c.y = x, y
    return c


def grab(b: Board, x: float, y: float, t0: float) -> float:
    """Pinch at (x, y) long enough to commit. Returns the time reached."""
    t = t0
    for _ in range(4):
        b.apply_hands([(x, y, True, False)], now=t)
        t += FRAME
    return t


def drag(b: Board, path, t: float, *, pinched=True) -> float:
    for x, y in path:
        b.apply_hands([(x, y, pinched, False)], now=t)
        t += FRAME
    return t


# --------------------------------------------------------------------------
# Point and say "this"
# --------------------------------------------------------------------------

class TestPointing:

    def test_an_open_hand_over_a_card_points_at_it(self, db):
        b = Board()
        c = card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.52, 0.5, False, False)], now=100.0)
        assert b.pointed(now=100.0)["id"] == c.id

    def test_pointing_uses_the_same_reach_as_grabbing(self, db):
        """So "this" means exactly the card a pinch would have picked up."""
        b = Board()
        card_at(b, "Far", 0.9, 0.9)
        b.apply_hands([(0.1, 0.1, False, False)], now=100.0)
        assert b.pointed(now=100.0) is None

    def test_it_is_remembered_through_the_sentence(self, db):
        """You point THEN speak. The hand can drop before the words arrive."""
        b = Board()
        card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)], now=100.0)
        b.apply_hands([], now=101.0)                     # hand drops
        got = b.pointed(now=100.0 + POINT_MEMORY_SECONDS - 0.5)
        assert got is not None and got["title"] == "Phone Stand"

    def test_it_is_forgotten_after_the_memory_window(self, db):
        b = Board()
        card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)], now=100.0)
        assert b.pointed(now=100.0 + POINT_MEMORY_SECONDS + 0.1) is None

    def test_the_age_travels_with_it(self, db):
        """So the model can tell "this, now" from "a while ago" and ask."""
        b = Board()
        card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)], now=100.0)
        assert b.pointed(now=103.0)["seconds_ago"] == 3.0

    def test_the_latest_point_wins(self, db):
        b = Board()
        card_at(b, "Left", 0.2, 0.5)
        right = card_at(b, "Right", 0.8, 0.5)
        b.apply_hands([(0.2, 0.5, False, False)], now=100.0)
        b.apply_hands([(0.8, 0.5, False, False)], now=100.5)
        assert b.pointed(now=100.5)["id"] == right.id

    def test_a_card_that_is_gone_is_not_pointed_at(self, db):
        b = Board()
        c = card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)], now=100.0)
        b.remove(c.id)
        assert b.pointed(now=100.0) is None


class TestPointingReachesApex:

    def test_the_companion_sends_the_pointed_object_with_its_age(self, db, monkeypatch):
        from agent import board as bm
        from dashboard.companion import workspace_message
        b = Board()
        card_at(b, "Phone Stand", 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, False)])
        monkeypatch.setattr(bm, "get_board", lambda: b)
        out = workspace_message({"workspace": "board"}, "what is this?")
        assert "pointed at" in out and "Phone Stand" in out
        assert "seconds_ago" in out
        assert "prefer the pointed object if it is recent" in out

    def test_without_a_board_workspace_nothing_is_added(self):
        from dashboard.companion import workspace_message
        assert workspace_message({}, "hello") == "hello"


# --------------------------------------------------------------------------
# Flick to throw away
# --------------------------------------------------------------------------

class TestTheFlickDecision:

    def test_fast_and_heading_off_the_board_is_a_flick(self):
        trail = [(0.0, 0.70, 0.5), (0.05, 0.80, 0.5), (0.10, 0.90, 0.5)]
        assert is_flick(trail)

    def test_fast_across_the_middle_is_not(self):
        """A brisk drag that ends mid-board is a drag, not a throw."""
        trail = [(0.0, 0.30, 0.5), (0.05, 0.40, 0.5), (0.10, 0.50, 0.5)]
        assert flick_velocity(trail)[0] >= FLICK_SPEED
        assert not is_flick(trail)

    def test_slow_to_the_edge_is_not(self):
        """Sliding a card carefully to the edge must not delete it."""
        trail = [(0.0, 0.90, 0.5), (0.05, 0.92, 0.5), (0.10, 0.94, 0.5)]
        assert not is_flick(trail)

    def test_frames_microseconds_apart_are_not_a_measurement(self):
        """The bug the existing drag test found: no real time, absurd speed."""
        trail = [(1.0, 0.3, 0.3), (1.000001, 0.7, 0.7)]
        assert flick_velocity(trail) is None
        assert not is_flick(trail)

    def test_too_little_trail_is_not_a_flick(self):
        assert not is_flick([])
        assert not is_flick([(0.0, 0.9, 0.5)])


class TestThrowingACardAway:

    def test_a_flick_off_the_edge_removes_it(self, db):
        b = Board()
        c = card_at(b, "Junk", 0.6, 0.5)
        t = grab(b, 0.6, 0.5, 100.0)
        t = drag(b, [(0.7, 0.5), (0.82, 0.5), (0.94, 0.5)], t)
        b.apply_hands([(0.99, 0.5, False, False)], now=t)          # let go, still moving
        assert c.id not in [x["id"] for x in b.cards()]

    def test_an_ordinary_set_down_does_not(self, db):
        """The one that must never go wrong."""
        b = Board()
        c = card_at(b, "Keep Me", 0.3, 0.5)
        t = grab(b, 0.3, 0.5, 100.0)
        t = drag(b, [(0.35, 0.5), (0.4, 0.5), (0.45, 0.5), (0.46, 0.5), (0.46, 0.5)], t)
        b.apply_hands([(0.46, 0.5, False, False)], now=t)
        assert c.id in [x["id"] for x in b.cards()]

    def test_a_card_carried_to_the_edge_and_stopped_stays(self, db):
        b = Board()
        c = card_at(b, "Parked", 0.7, 0.5)
        t = grab(b, 0.7, 0.5, 100.0)
        t = drag(b, [(0.8, 0.5), (0.9, 0.5), (0.95, 0.5), (0.95, 0.5), (0.95, 0.5), (0.95, 0.5)], t)
        b.apply_hands([(0.95, 0.5, False, False)], now=t)
        assert c.id in [x["id"] for x in b.cards()]

    def test_undo_brings_it_back_to_where_it_came_from(self, db):
        """Not to the edge it was flung at — to where it was picked up."""
        b = Board()
        c = card_at(b, "Junk", 0.6, 0.4)
        t = grab(b, 0.6, 0.4, 100.0)
        t = drag(b, [(0.7, 0.4), (0.82, 0.4), (0.94, 0.4)], t)
        b.apply_hands([(0.99, 0.4, False, False)], now=t)
        b.undo()
        back = next(x for x in b.cards() if x["id"] == c.id)
        assert (round(back["x"], 3), round(back["y"], 3)) == (0.6, 0.4)

    def test_the_page_is_told(self, db):
        b = Board()
        card_at(b, "Junk", 0.6, 0.5)
        seq = b.latest_event_seq()
        t = grab(b, 0.6, 0.5, 100.0)
        t = drag(b, [(0.7, 0.5), (0.82, 0.5), (0.94, 0.5)], t)
        b.apply_hands([(0.99, 0.5, False, False)], now=t)
        ev = b.events_since(seq)
        assert [e["type"] for e in ev] == ["thrown"] and ev[0]["title"] == "Junk"

    def test_flinging_the_hand_out_of_frame_throws_too(self, db):
        """The most natural throw there is: the hand leaves the camera."""
        b = Board()
        c = card_at(b, "Junk", 0.6, 0.5)
        t = grab(b, 0.6, 0.5, 100.0)
        t = drag(b, [(0.7, 0.5), (0.82, 0.5), (0.94, 0.5)], t)
        b.apply_hands([], now=t)
        assert c.id not in [x["id"] for x in b.cards()]

    def test_an_open_hand_at_the_end_of_a_fling_is_a_throw_not_a_cancel(self, db):
        """Open palm is the cancel gesture — held still. A hand that opens
        while flinging is finishing a throw; reading it as cancel would snap
        the card back the instant you tried to throw it."""
        b = Board()
        c = card_at(b, "Junk", 0.6, 0.5)
        t = grab(b, 0.6, 0.5, 100.0)
        t = drag(b, [(0.7, 0.5), (0.82, 0.5), (0.94, 0.5)], t)
        b.apply_hands([(0.99, 0.5, False, True)], now=t)
        assert c.id not in [x["id"] for x in b.cards()]
        # And undo still brings it HOME. The cancel branch consumes the
        # pre-grab position; had it run here, undo would restore the card at
        # the edge it was flung at. The first version of this test checked
        # only that the card was gone, and passed with that bug in place.
        b.undo()
        back = next(x for x in b.cards() if x["id"] == c.id)
        assert round(back["x"], 3) == 0.6

    def test_a_still_open_palm_still_cancels(self, db):
        b = Board()
        c = card_at(b, "Keep", 0.4, 0.5)
        t = grab(b, 0.4, 0.5, 100.0)
        t = drag(b, [(0.45, 0.5), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)], t)
        b.apply_hands([(0.5, 0.5, False, True)], now=t)
        back = next(x for x in b.cards() if x["id"] == c.id)
        assert round(back["x"], 3) == 0.4

    def test_a_two_handed_hold_cannot_be_flicked(self, db):
        """Two hands sweeping a card fast toward the edge and letting go is a
        two-handed move, not a throw — two hands on something is how you hold
        it carefully.

        Both hands move TOGETHER here. The first version of this test pulled
        them apart symmetrically, so their midpoint never moved and there was
        no speed to measure even with every guard removed — it could not fail.
        """
        b = Board()
        c = card_at(b, "Big", 0.5, 0.5)
        t = 100.0
        for _ in range(4):
            b.apply_hands([(0.45, 0.5, True, False), (0.55, 0.5, True, False)], now=t)
            t += FRAME
        for dx in (0.1, 0.22, 0.34, 0.46):
            b.apply_hands([(0.45 + dx, 0.5, True, False), (0.55 + dx, 0.5, True, False)], now=t)
            t += FRAME
        b.apply_hands([], now=t)
        assert c.id in [x["id"] for x in b.cards()]


# --------------------------------------------------------------------------
# Swipes
# --------------------------------------------------------------------------

class TestSwipes:

    def test_left_and_right_step_through_cards(self, db):
        from agent.handtrack import run_board_action
        b = Board()
        a = card_at(b, "A", 0.2, 0.5)
        c2 = card_at(b, "B", 0.5, 0.5)
        run_board_action("board:next", b)             # nothing selected: latest
        assert b.selection()["id"] == c2.id
        run_board_action("board:next", b)             # wraps
        assert b.selection()["id"] == a.id
        run_board_action("board:prev", b)
        assert b.selection()["id"] == c2.id

    def test_up_summons_apex(self, db):
        from agent.handtrack import run_board_action
        b = Board()
        seq = b.latest_event_seq()
        assert run_board_action("board:summon", b) == "summoned Apex"
        assert [e["type"] for e in b.events_since(seq)] == ["summon"]

    def test_a_swipe_while_holding_a_card_is_ignored(self, db):
        """Dragging a card IS a swipe to the recognizer."""
        from agent.handtrack import run_board_action
        b = Board()
        card_at(b, "A", 0.5, 0.5)
        card_at(b, "B", 0.2, 0.5)
        grab(b, 0.5, 0.5, 100.0)
        before = b.selection()
        assert run_board_action("board:next", b).startswith("ignored")
        assert b.selection() == before

    def test_a_swipe_right_after_a_throw_is_ignored(self, db, monkeypatch):
        """The tail of the flick that threw a card reads as a swipe."""
        from agent.handtrack import run_board_action
        b = Board()
        card_at(b, "Keep", 0.2, 0.5)
        card_at(b, "Junk", 0.6, 0.5)
        t = grab(b, 0.6, 0.5, 100.0)
        t = drag(b, [(0.7, 0.5), (0.82, 0.5), (0.94, 0.5)], t)
        b.apply_hands([(0.99, 0.5, False, False)], now=t)
        monkeypatch.setattr(board_mod.time, "time", lambda: t + 0.2)
        assert run_board_action("board:next", b).startswith("ignored")

    def test_an_unknown_board_action_says_so(self, db):
        from agent.handtrack import run_board_action
        b = Board()
        assert "unknown board action" in run_board_action("board:dance", b)

    def test_board_swipes_are_mapped_by_default(self):
        from agent import gestures
        assert gestures.gesture_action("swipe_up") == "board:summon"
        assert gestures.gesture_action("swipe_left") == "board:prev"
        assert gestures.gesture_action("swipe_right") == "board:next"

    def test_board_gestures_act_without_resident_mode(self, db, monkeypatch):
        """on_gesture is only ever wired by app/resident.py. In main.py --text
        it is None — and every mapped gesture used to be logged and then do
        nothing. Board gestures must not depend on it."""
        import threading
        import config
        from agent import handtrack as ht, board as bm
        b = Board()
        card_at(b, "A", 0.5, 0.5)
        monkeypatch.setattr(bm, "get_board", lambda: b)
        monkeypatch.setattr(config, "BOARD_ENABLED", True)
        t = ht.HandTracker.__new__(ht.HandTracker)
        t._lock = threading.Lock()
        t.on_gesture = None
        logged = []
        t.log = type("L", (), {"add": lambda self, k, v: logged.append(v)})()
        t._dispatch("swipe_right")
        assert b.selection() is not None
        assert any("board:next" in line for line in logged)


# --------------------------------------------------------------------------
# Summon at your hand
# --------------------------------------------------------------------------

class TestSummonAtYourHand:

    def test_a_recent_hand_is_where_it_appears(self, db):
        b = Board()
        b.apply_hands([(0.3, 0.6, False, False)], now=100.0)
        assert b.hand_anchor(now=101.0) == (0.3, 0.6)

    def test_no_recent_hand_means_the_default_spot(self, db):
        b = Board()
        b.apply_hands([(0.3, 0.6, False, False)], now=100.0)
        assert b.hand_anchor(now=200.0) is None

    def test_a_hand_at_the_edge_keeps_the_card_on_the_board(self, db):
        b = Board()
        b.apply_hands([(0.99, 0.02, False, False)], now=100.0)
        x, y = b.hand_anchor(now=100.0)
        assert x <= 1.0 - CARD_W / 2 and y > 0.02

    def test_board_present_uses_it(self, db, monkeypatch):
        from agent import core, board as bm
        b = Board()
        monkeypatch.setattr(bm, "get_board", lambda: b)
        b.apply_hands([(0.3, 0.6, False, False)])
        out = core._execute_tool("board_present", {"title": "Calendar", "body": "3pm call"})
        card = next(c for c in b.cards() if c["title"] == "Calendar")
        assert (round(card["x"], 3), round(card["y"], 3)) == (0.3, 0.6)
        assert "at your hand" in out

    def test_board_present_without_a_hand_uses_the_default(self, db, monkeypatch):
        from agent import core, board as bm
        b = Board()
        monkeypatch.setattr(bm, "get_board", lambda: b)
        out = core._execute_tool("board_present", {"title": "Calendar"})
        card = next(c for c in b.cards() if c["title"] == "Calendar")
        assert (card["x"], card["y"]) == (0.5, 0.35)
        assert "at your hand" not in out


# --------------------------------------------------------------------------
# The socket
# --------------------------------------------------------------------------

class TestEvents:

    def test_events_are_numbered_and_filtered(self, db):
        b = Board()
        b.emit("summon")
        seq = b.latest_event_seq()
        b.emit("selected", id="x", title="A")
        got = b.events_since(seq)
        assert [e["type"] for e in got] == ["selected"]

    def test_the_backlog_is_bounded(self, db):
        b = Board()
        for _ in range(board_mod.EVENT_BACKLOG + 20):
            b.emit("summon")
        assert len(b.events_since(0)) == board_mod.EVENT_BACKLOG
