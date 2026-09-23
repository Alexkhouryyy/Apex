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


class TestHandIdentities:
    """Identity by position. MediaPipe's label flips and its detection order
    renumbers; keying on either moved a held card to the other hand."""

    def test_the_same_hand_keeps_its_id_as_it_moves(self):
        ids = ht.HandIdentities()
        a = ids.assign([(0.5, 0.5)], 100.0)
        b = ids.assign([(0.55, 0.5)], 100.05)
        assert a == b

    def test_detection_order_does_not_matter(self):
        ids = ht.HandIdentities()
        first = ids.assign([(0.2, 0.5), (0.8, 0.5)], 100.0)
        swapped = ids.assign([(0.8, 0.5), (0.2, 0.5)], 100.05)
        assert swapped == [first[1], first[0]]

    def test_a_new_hand_gets_a_new_id_and_takes_nobody_elses(self):
        ids = ht.HandIdentities()
        (held,) = ids.assign([(0.7, 0.5)], 100.0)
        new, still = ids.assign([(0.3, 0.5), (0.7, 0.5)], 100.05)
        assert still == held and new != held

    def test_a_missed_frame_keeps_the_id(self):
        ids = ht.HandIdentities()
        (a,) = ids.assign([(0.5, 0.5)], 100.0)
        ids.assign([], 100.05)
        (b,) = ids.assign([(0.52, 0.5)], 100.10)
        assert a == b

    def test_a_hand_gone_past_the_grace_is_a_new_hand(self):
        ids = ht.HandIdentities()
        (a,) = ids.assign([(0.5, 0.5)], 100.0)
        (b,) = ids.assign([(0.5, 0.5)], 100.0 + ht.HAND_LOSS_GRACE_SECONDS + 0.05)
        assert a != b


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
        t._read_hands(frame(hand(0.40)), now=100.0)
        t._read_hands(frame(), now=100.05)             # hand out of frame...
        t._read_hands(frame(), now=100.4)              # ...for real
        _c, details = t._read_hands(frame(hand(0.74)), now=100.45)
        assert details[0]["pinched"] is False

    def test_one_missed_detection_keeps_the_pinch(self):
        """The other side of the same rule. Motion blur on a fast drag makes
        MediaPipe miss a frame; forgetting the latch on it meant the hand came
        back in the 0.70-0.78 band reading OPEN, and the card was dropped."""
        t = tracker()
        t._read_hands(frame(hand(0.40)), now=100.0)
        t._read_hands(frame(), now=100.05)             # missed one frame
        _c, details = t._read_hands(frame(hand(0.74)), now=100.10)
        assert details[0]["pinched"] is True

    def test_a_label_flip_does_not_reset_the_pinch(self):
        """MediaPipe calls the same hand Right, then Left for one frame. The
        latch used to be keyed on that label, so the flip read as a new,
        open hand at 0.74 and dropped the card."""
        t = tracker()
        t._read_hands(frame(hand(0.40), labels=["Right"]), now=100.0)
        _c, details = t._read_hands(frame(hand(0.74), labels=["Left"]), now=100.05)
        assert details[0]["pinched"] is True

    def test_swapped_detection_order_keeps_each_hands_pinch(self):
        """Right pinched, Left open; next frame MediaPipe lists them the other
        way round, both at 0.74. Right is still pinched, Left still is not."""
        t = tracker()
        t._read_hands(frame(hand(0.40, 0.7, 0.5), hand(0.95, 0.3, 0.5),
                            labels=["Right", "Left"]), now=100.0)
        _c, details = t._read_hands(frame(hand(0.74, 0.3, 0.5), hand(0.74, 0.7, 0.5),
                                          labels=["Left", "Right"]), now=100.05)
        by_x = {round(d["x"], 1): d["pinched"] for d in details}
        assert by_x == {0.7: True, 0.3: False}


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
        t._read_hands(frame(hand(0.40, 0.2, 0.5), hand(0.95, 0.8, 0.5),
                            labels=["Right", "Left"]), now=100.0)
        cursors, details = t._read_hands(frame(hand(0.95, 0.8, 0.5), hand(0.40, 0.2, 0.5),
                                               labels=["Left", "Right"]), now=100.05)
        assert [d["label"] for d in details] == ["Right", "Left"], \
            "order must follow the hand, not MediaPipe's listing"
        for c, d in zip(cursors, details):
            assert (round(c[0], 4), round(c[1], 4)) == (d["x"], d["y"])
            assert c[4] == d["id"]


class TestTheRealFrameLoop:
    """Every other test here calls `_read_hands` directly. This one drives
    `HandTracker._tick` — camera read, MediaPipe call, latch, board — with only
    the camera and the model faked, so a `_tick` that stopped using the
    latched, identity-keyed `_read_hands` (or stopped passing its result to
    the board) fails here even though every unit above still passes."""

    def test_a_wobbly_pinch_through_tick_keeps_the_card(self, db, monkeypatch):
        import numpy as np
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        b = Board()
        card = b.add("note", "Grab Me", src="")
        card.x, card.y = 0.5, 0.5
        monkeypatch.setattr(board_mod, "get_board", lambda: b)

        frames = iter([frame(hand(r)) for r in WOBBLY_HOLD]
                      + [frame(), frame(hand(0.74))])     # one missed detection

        class Cap:
            def read(self):
                return True, np.zeros((4, 4, 3), dtype=np.uint8)

        class Landmarker:
            def detect_for_video(self, image, ts):
                return next(frames)

        class MP:
            class ImageFormat:
                SRGB = 1

            @staticmethod
            def Image(image_format, data):
                return data

        from agent.awareness import AwarenessLog
        t = ht.HandTracker(AwarenessLog())
        t._cap, t._landmarker, t._mp = Cap(), Landmarker(), MP()
        monkeypatch.setattr(t, "_open", lambda now: True)
        held = []
        for i in range(len(WOBBLY_HOLD) + 2):
            t._tick(100.0 + i * 0.05)
            held.append(bool(card.held_by))
        first = held.index(True)
        assert all(held[first:]), f"dropped mid-hold: {held}"
        assert t.latest_hands()[0]["pinched"] is True


class TestTheReleaseFollowsTheEntry:
    """Release used to be a fixed 0.78 whatever HANDTRACK_PINCH_RATIO said,
    and the calibrator only rewrites the entry: a calibrated 0.80 collapsed
    the band to nothing, and a calibrated 0.55 left a release your hand might
    never rise above while holding."""

    def test_unset_release_is_entry_plus_the_margin(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", None)
        assert ht.pinch_release_ratio(0.70) == pytest.approx(0.78)
        assert ht.pinch_release_ratio(0.80) == pytest.approx(0.88)

    def test_an_explicit_release_is_used(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", 0.75)
        assert ht.pinch_release_ratio(0.70) == pytest.approx(0.75)

    def test_a_release_below_entry_means_no_hysteresis_not_nonsense(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", 0.60)
        assert ht.pinch_release_ratio(0.70) == pytest.approx(0.70)

    def test_the_frame_loop_uses_the_derived_release(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.80)
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", None)
        t = tracker()
        t._read_hands(frame(hand(0.40)), now=100.0)
        _c, details = t._read_hands(frame(hand(0.84)), now=100.05)
        assert details[0]["release"] == pytest.approx(0.88)
        assert details[0]["pinched"] is True, "0.84 is inside the band for entry 0.80"


def test_the_calibrator_places_release_between_entry_and_the_open_hand():
    from scripts import calibrate_pinch as cal
    open_samples = [0.83, 0.85, 0.9, 0.95, 1.0] * 6
    release = cal.recommend_release(0.70, open_samples)
    assert 0.70 < release < 0.83
