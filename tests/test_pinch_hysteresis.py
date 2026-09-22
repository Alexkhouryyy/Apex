"""Pinch hysteresis: why a held card was dropped, and the test that proves it no longer is.

On real hardware, two-handed grabs succeeded 70% of the time, and the user
reported every miss the same way: picked up, then dropped.

The cause was one threshold decided afresh every frame:

    pinched = ratio < 0.70

A real pinch does not sit still. It reads 0.66, 0.72, 0.69, 0.71 on successive
frames, and each frame above 0.70 was an "open" frame. On the board, one open
frame removes that hand from the card it holds:

    kept = [i for i in c.held_by if hands[i][2]]

so the card fell mid-move. The board's own module comment cites a gesture
contract of "confidence, dwell time, hysteresis, and cooldown". Dwell was
built; hysteresis was not.

These tests drive the REAL path — synthetic landmarks through
`HandTracker._read_hands` into `Board.apply_hands` — rather than testing the
latch alone, because the bug only existed in how those pieces met.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import config
from agent import board as board_mod
from agent import handtrack as ht
from agent.board import Board, ARM_DWELL_SECONDS, HandState


# --------------------------------------------------------------------------
# Synthetic hands that produce an exact pinch ratio
# --------------------------------------------------------------------------

def hand(ratio: float | None, x: float = 0.5, y: float = 0.5):
    """21 landmarks whose thumb-to-index gap / wrist-to-middle span == ratio.

    Span is fixed at 0.2. The index tip — which is the cursor — sits at (x, y)
    in selfie space, so a card placed at (x, y) is under the hand.
    """
    pts = [SimpleNamespace(x=0.5, y=0.5) for _ in range(21)]
    raw_x = 1.0 - x                       # undo the mirror landmarks_to_cursor applies
    pts[ht.WRIST] = SimpleNamespace(x=raw_x, y=y + 0.2)
    pts[ht.MIDDLE_MCP] = SimpleNamespace(x=raw_x, y=y)
    pts[ht.INDEX_TIP] = SimpleNamespace(x=raw_x, y=y)
    gap = 0.2 * (ratio if ratio is not None else 0.0)
    pts[ht.THUMB_TIP] = SimpleNamespace(x=raw_x + gap, y=y)
    if ratio is None:                     # unusable: a degenerate span
        pts[ht.MIDDLE_MCP] = SimpleNamespace(x=raw_x, y=y + 0.2)
    return pts


def frame(*hands, labels=None):
    labels = labels or [["Right", "Left"][i % 2] for i in range(len(hands))]
    return SimpleNamespace(
        hand_landmarks=list(hands),
        handedness=[[SimpleNamespace(category_name=l)] for l in labels])


def tracker():
    t = ht.HandTracker.__new__(ht.HandTracker)
    t._lock = threading.Lock()
    return t


@pytest.fixture(autouse=True)
def thresholds(monkeypatch):
    monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.70)
    monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", 0.78)
    monkeypatch.setattr(config, "HANDTRACK_MIRROR", True)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "b.db"))
    longterm.init_db()
    board_mod.init_db()


def test_the_synthetic_hand_measures_what_it_claims():
    """Everything below rests on this."""
    for r in (0.30, 0.69, 0.71, 0.77, 0.79, 0.95):
        assert ht.pinch_ratio(hand(r)) == pytest.approx(r)


# --------------------------------------------------------------------------
# The latch
# --------------------------------------------------------------------------

class TestPinchLatch:

    def test_a_pinch_starts_below_the_entry_threshold(self):
        l = ht.PinchLatch()
        assert l.update("R", 0.71, 0.70, 0.78) is False
        assert l.update("R", 0.69, 0.70, 0.78) is True

    def test_a_held_pinch_survives_hovering_between_the_two(self):
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.78)
        for r in (0.72, 0.75, 0.71, 0.77):
            assert l.update("R", r, 0.70, 0.78) is True, f"dropped at {r}"

    def test_it_ends_only_above_the_release_threshold(self):
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.78)
        assert l.update("R", 0.79, 0.70, 0.78) is False

    def test_an_open_hand_hovering_between_does_not_start_one(self):
        """The asymmetry is the point: entry is not loosened, so an open hand
        drifting to 0.75 does not suddenly count as a pinch."""
        l = ht.PinchLatch()
        for r in (0.95, 0.75, 0.72, 0.76):
            assert l.update("R", r, 0.70, 0.78) is False

    def test_an_unreadable_frame_keeps_the_state_it_had(self):
        """A missing observation is not an observation that the hand opened —
        the rule _tick already applies to a dropped camera frame."""
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.78)
        assert l.update("R", None, 0.70, 0.78) is True
        l2 = ht.PinchLatch()
        assert l2.update("R", None, 0.70, 0.78) is False

    def test_a_release_set_below_entry_is_treated_as_entry(self):
        """Without the clamp, a release of 0.60 would drop a held pinch reading
        0.65 — a hand pinched ENOUGH to start a grab but not enough to keep it,
        which is nonsense. The earlier version of this test only probed 0.71,
        where clamped and unclamped agree, so it passed with the clamp removed."""
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.60)
        assert l.update("R", 0.65, 0.70, 0.60) is True

    def test_release_at_or_below_entry_means_no_hysteresis(self):
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.70)
        assert l.update("R", 0.71, 0.70, 0.70) is False
        l2 = ht.PinchLatch()
        l2.update("R", 0.40, 0.70, 0.60)
        assert l2.update("R", 0.71, 0.70, 0.60) is False

    def test_hands_are_latched_separately(self):
        l = ht.PinchLatch()
        l.update("Right", 0.40, 0.70, 0.78)
        assert l.update("Left", 0.74, 0.70, 0.78) is False
        assert l.update("Right", 0.74, 0.70, 0.78) is True

    def test_a_hand_that_leaves_comes_back_open(self):
        l = ht.PinchLatch()
        l.update("R", 0.40, 0.70, 0.78)
        l.keep_only([])
        assert l.update("R", 0.74, 0.70, 0.78) is False


class TestHandKeys:

    def test_distinct_labels_are_used(self):
        assert ht.hand_keys(["Right", "Left"]) == ["Right", "Left"]

    def test_duplicate_labels_fall_back_to_position(self):
        """Two hands both called "Right" would otherwise share one latch."""
        assert ht.hand_keys(["Right", "Right"]) == ["#0", "#1"]

    def test_a_missing_label_falls_back_to_position(self):
        assert ht.hand_keys(["Right", ""]) == ["#0", "#1"]


# --------------------------------------------------------------------------
# The real path, landmarks to board
# --------------------------------------------------------------------------

# What a real, slightly wobbly pinch looks like: in, then hovering around 0.70.
WOBBLY_HOLD = [0.40, 0.45, 0.66, 0.72, 0.69, 0.74, 0.71, 0.76, 0.70, 0.73]


class TestTheCardIsNotDropped:

    def _drive(self, ratios, *, start=100.0):
        b = Board()
        card = b.add("note", "Grab Me", src="")
        card.x, card.y = 0.5, 0.5
        t = tracker()
        held = []
        for i, r in enumerate(ratios):
            cursors, _details = t._read_hands(frame(hand(r)))
            b.apply_hands(cursors, now=start + i * 0.1)
            held.append(bool(card.held_by))
        return held

    def test_a_wobbly_pinch_keeps_holding(self, db):
        """The user's report, replayed. Before this change every frame above
        0.70 dropped the card."""
        held = self._drive(WOBBLY_HOLD)
        first = held.index(True)
        assert all(held[first:]), f"dropped mid-hold: {held}"

    def test_the_old_single_threshold_drops_it(self, db, monkeypatch):
        """The same frames with hysteresis off — this is what the 30% was."""
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", 0.70)
        held = self._drive(WOBBLY_HOLD)
        first = held.index(True)
        assert not all(held[first:]), "expected the single threshold to drop it"

    def test_opening_the_hand_still_lets_go(self, db):
        """The cost of hysteresis is a slightly wider release. It must not be
        so wide that an ordinary open hand cannot let go."""
        held = self._drive([0.40, 0.40, 0.40, 0.85, 0.92])
        assert held[2] is True and held[-1] is False

    def test_two_hands_survive_wobble_together(self, db):
        """The two-handed case the 70% was measured on. Either hand
        flickering used to break the pair."""
        b = Board()
        card = b.add("note", "Grab Me", src="")
        card.x, card.y = 0.5, 0.5
        t = tracker()
        left_wobble = [0.40, 0.45, 0.72, 0.69, 0.75, 0.71, 0.74, 0.70]
        right_wobble = [0.42, 0.40, 0.66, 0.73, 0.70, 0.76, 0.69, 0.72]
        pairs = []
        for i, (lr, rr) in enumerate(zip(left_wobble, right_wobble)):
            cursors, _ = t._read_hands(frame(hand(rr, 0.5, 0.5), hand(lr, 0.5, 0.5),
                                             labels=["Right", "Left"]))
            b.apply_hands(cursors, now=200.0 + i * 0.1)
            pairs.append(len(card.held_by))
        first = pairs.index(2)
        assert all(n == 2 for n in pairs[first:]), f"the pair broke: {pairs}"


class TestAHandThatLeavesComesBackOpen:

    def test_the_frame_loop_forgets_a_hand_that_left(self):
        """Tested through `_read_hands`, not the latch alone: the latch had a
        test for keep_only and the loop could stop calling it with every test
        still passing. A hand pinched, lowered out of frame and raised again
        reading 0.74 is NOT pinching — it is a hand coming back up."""
        t = tracker()
        t._read_hands(frame(hand(0.40)))
        t._read_hands(frame())                         # hand out of frame
        _c, details = t._read_hands(frame(hand(0.74)))
        assert details[0]["pinched"] is False


class TestTheReadoutStaysHonest:

    def test_details_carry_both_thresholds(self):
        _c, details = tracker()._read_hands(frame(hand(0.40)))
        assert details[0]["threshold"] == 0.70
        assert details[0]["release"] == 0.78

    def test_details_report_the_latched_state_not_the_raw_one(self):
        """At 0.74 while held the hand IS pinched. A readout that said
        otherwise would send someone tuning a threshold that is working."""
        t = tracker()
        t._read_hands(frame(hand(0.40)))
        _c, details = t._read_hands(frame(hand(0.74)))
        assert details[0]["pinched"] is True

    def test_details_are_ordered_with_the_cursors(self):
        """They used to stay in detection order while cursors were sorted by
        handedness, so the panel could show the left hand's ratio next to the
        right hand's grab."""
        t = tracker()
        cursors, details = t._read_hands(frame(hand(0.40, 0.2, 0.5), hand(0.95, 0.8, 0.5),
                                               labels=["Right", "Left"]))
        assert [d["label"] for d in details] == ["Left", "Right"]
        for c, d in zip(cursors, details):
            assert (round(c[0], 4), round(c[1], 4)) == (d["x"], d["y"])
