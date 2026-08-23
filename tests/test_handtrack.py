"""Apex's own MediaPipe hand tracker.

There is no camera here and never will be in CI, so the split is the same one
`tests/test_iot_watcher.py` established: everything that decides anything is a
pure function fed synthetic landmarks, and the half that needs a webcam is named
as unproven rather than assumed.

What is NOT covered: real light hitting a real lens. What IS covered: the
geometry that turns 21 landmarks into a cursor, which is where an inverted axis
or a scale-dependent threshold would live and would be nearly impossible to
debug by waving at a screen.

The landmark shape used below was read off the installed MediaPipe package
(`NormalizedLandmark` has x/y/z, `Category` has category_name/score), not
recalled — an API assumed rather than checked is how the last several bugs got
in.
"""
import types

import pytest

import config
from agent import gestures, handtrack


def _lm(x, y, z=0.0):
    return types.SimpleNamespace(x=x, y=y, z=z)


def _hand(*, pinch_gap=0.5, span=0.25, x=0.5, y=0.5):
    """21 landmarks with only the four that matter placed deliberately.

    `pinch_gap` is thumb-to-index distance and `span` is wrist-to-middle-knuckle,
    both in normalized units, so a test can set the RATIO the code actually uses
    instead of pixel positions that only look meaningful.
    """
    lms = [_lm(0.0, 0.0) for _ in range(21)]
    lms[handtrack.WRIST] = _lm(x, y + span)
    lms[handtrack.MIDDLE_MCP] = _lm(x, y)
    lms[handtrack.INDEX_TIP] = _lm(x, y)
    lms[handtrack.THUMB_TIP] = _lm(x + pinch_gap, y)
    return lms


class TestPinchRatio:
    def test_pinch_is_scale_invariant(self):
        """THE reason pinch is a ratio and not a pixel distance.

        The same gesture at arm's length and up close differs by a large factor
        in pixels. An absolute threshold would make pinch depend on how far away
        you sit — it would work while you tuned it and fail when you leaned back.
        """
        near = handtrack.pinch_ratio(_hand(pinch_gap=0.40, span=0.20))
        far = handtrack.pinch_ratio(_hand(pinch_gap=0.10, span=0.05))
        assert near == pytest.approx(far), "same hand shape, different distance"

    def test_a_closed_pinch_ratios_near_zero(self):
        assert handtrack.pinch_ratio(_hand(pinch_gap=0.01, span=0.25)) < 0.1

    def test_an_open_hand_ratios_high(self):
        assert handtrack.pinch_ratio(_hand(pinch_gap=0.30, span=0.25)) > 1.0

    def test_a_degenerate_span_does_not_divide_by_zero(self):
        """A hand seen edge-on can collapse wrist and knuckle onto one point.
        This runs in the tracker thread, so a ZeroDivisionError would kill hand
        tracking for the session with nothing but a dead thread to show."""
        assert handtrack.pinch_ratio(_hand(span=0.0)) is None

    @pytest.mark.parametrize("bad", [[], None, "not landmarks", [_lm(0, 0)]])
    def test_garbage_landmarks_do_not_raise(self, bad):
        assert handtrack.pinch_ratio(bad) is None


class TestLandmarksToCursor:
    def test_the_index_fingertip_is_the_cursor(self):
        lms = _hand(x=0.3, y=0.7)
        lms[handtrack.INDEX_TIP] = _lm(0.25, 0.75)
        x, y, _p = handtrack.landmarks_to_cursor(lms, mirror=False)
        assert (x, y) == pytest.approx((0.25, 0.75))

    def test_mirroring_flips_x_and_only_x(self):
        """THE inverted-axis guard.

        A raw webcam frame is not mirrored. Without the flip, moving your hand
        to your right moves the point LEFT in the image, so swipe_right fires
        for a leftward wave. That is maddening to debug by waving at a screen
        and trivial to pin here.
        """
        lms = _hand()
        lms[handtrack.INDEX_TIP] = _lm(0.2, 0.6)
        plain = handtrack.landmarks_to_cursor(lms, mirror=False)
        flipped = handtrack.landmarks_to_cursor(lms, mirror=True)
        assert plain[0] == pytest.approx(0.2)
        assert flipped[0] == pytest.approx(0.8)
        assert plain[1] == flipped[1], "y must not be touched"

    def test_a_hand_moving_right_reads_as_swipe_right(self):
        """The end-to-end statement of the same thing, through the recognizer —
        because the flip being correct in isolation does not prove the sign
        survives into the gesture."""
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        fired = []
        for i in range(16):
            lms = _hand()
            # In IMAGE space the hand travels LEFT, because the user moving
            # right appears to move left in an unmirrored frame.
            lms[handtrack.INDEX_TIP] = _lm(0.9 - i * 0.05, 0.5)
            cur = handtrack.landmarks_to_cursor(lms, mirror=True)
            fired += rec.feed_cursors([cur], 1000.0 + i * 0.05)
        assert "swipe_right" in fired, fired

    def test_coordinates_are_clamped_into_the_frame(self):
        """MediaPipe can report slightly outside the frame for a hand at the
        edge. Window fractions have to stay window fractions or every threshold
        downstream is measured against the wrong scale."""
        lms = _hand()
        lms[handtrack.INDEX_TIP] = _lm(1.4, -0.3)
        x, y, _ = handtrack.landmarks_to_cursor(lms, mirror=False)
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0

    def test_the_pinch_threshold_is_configurable(self, monkeypatch):
        """The shipped default was chosen with no camera to test against, so it
        has to be movable without editing code."""
        lms = _hand(pinch_gap=0.10, span=0.25)      # ratio 0.4
        assert handtrack.landmarks_to_cursor(lms, threshold=0.5)[2] is True
        assert handtrack.landmarks_to_cursor(lms, threshold=0.3)[2] is False

    def test_the_configured_threshold_is_actually_read(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.9, raising=False)
        lms = _hand(pinch_gap=0.15, span=0.25)      # ratio 0.6
        assert handtrack.landmarks_to_cursor(lms)[2] is True
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.1, raising=False)
        assert handtrack.landmarks_to_cursor(lms)[2] is False

    @pytest.mark.parametrize("bad", [[], None, [_lm(0, 0)]])
    def test_garbage_yields_no_cursor_rather_than_raising(self, bad):
        assert handtrack.landmarks_to_cursor(bad) is None


class TestHandedness:
    def test_hands_are_ordered_by_label_not_detection_order(self):
        """Detection order carries no identity — it is what makes a second hand
        look like a screen-wide swipe. Handedness is better evidence than
        proximity, and MediaPipe gives it away for free."""
        left, right = (0.2, 0.5, False), (0.8, 0.5, False)
        a = handtrack.order_by_handedness([left, right], ["Left", "Right"])
        b = handtrack.order_by_handedness([right, left], ["Right", "Left"])
        assert a == b, "the same two hands must land in the same slots"

    @pytest.mark.parametrize("labels", [[], ["Left"], ["", ""], None])
    def test_missing_labels_fall_back_to_the_given_order(self, labels):
        """Handedness is an improvement, not a dependency — the recognizer's
        proximity pairing still covers the case where MediaPipe won't say."""
        cursors = [(0.2, 0.5, False), (0.8, 0.5, False)]
        assert handtrack.order_by_handedness(cursors, labels) == cursors


class TestAvailability:
    def test_it_names_which_piece_is_missing(self, monkeypatch):
        """Two dependencies, two different fixes. One unhelpful False would make
        the user guess which."""
        import sys
        monkeypatch.setitem(sys.modules, "mediapipe", None)
        ok, why = handtrack.available()
        if not ok:
            assert "mediapipe" in why


class TestOpenCVConflict:
    """`pip install mediapipe` pulls opencv-contrib-python; requirements.txt
    pins opencv-python-headless. Both write into the SAME cv2 directory, so the
    second install overwrites files from the first. Nothing errors — cv2 imports
    and quietly misbehaves, which is unbudgeted debugging time unless something
    names it."""

    def test_one_opencv_is_not_a_conflict(self, monkeypatch):
        monkeypatch.setattr(handtrack, "_dists_installed",
                            lambda: {"opencv-contrib-python", "numpy"},
                            raising=False)
        assert handtrack.opencv_conflict() == []

    def test_two_opencvs_are_named(self, monkeypatch):
        monkeypatch.setattr(handtrack, "_dists_installed",
                            lambda: {"opencv-python-headless",
                                     "opencv-contrib-python"},
                            raising=False)
        found = handtrack.opencv_conflict()
        assert len(found) == 2
        assert "opencv-contrib-python" in found
        assert "opencv-python-headless" in found

    def test_a_broken_metadata_read_does_not_raise(self, monkeypatch):
        """This runs on the tracker's startup path. A raise here would stop
        hand tracking over a diagnostic."""
        def _boom():
            raise RuntimeError("metadata is unreadable")
        monkeypatch.setattr(handtrack, "_dists_installed", _boom, raising=False)
        assert handtrack.opencv_conflict() == []


class TestCameraCoexistence:
    """The webcam is exclusive. While the tracker holds it, a second
    VideoCapture fails — including Apex's own camera_capture tool, which would
    be two halves of one program fighting over one device."""

    def test_camera_capture_prefers_the_tracker_frame(self, monkeypatch):
        import numpy as np
        from tools import camera
        monkeypatch.setattr(config, "CAMERA_ENABLED", True, raising=False)
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        camera.set_tracker_frame_source(lambda: frame)

        def _no_device(*a, **k):
            raise AssertionError("must not open the device while tracking")
        monkeypatch.setattr("cv2.VideoCapture", _no_device)
        try:
            b64, size = camera.capture()
            assert b64 and size == (8, 8)
        finally:
            camera.set_tracker_frame_source(None)

    def test_without_a_tracker_it_opens_the_device_as_before(self, monkeypatch):
        from tools import camera
        camera.set_tracker_frame_source(None)
        monkeypatch.setattr(config, "CAMERA_ENABLED", True, raising=False)
        opened = []

        class _Cap:
            def isOpened(self): opened.append(True); return False
        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: _Cap())
        with pytest.raises(RuntimeError) as e:
            camera.capture()
        assert opened, "the device should still be tried when nothing is tracking"
        assert "release_camera" in str(e.value), \
            "the error must say what is holding the camera"

    def test_a_raising_tracker_falls_back_to_the_device(self, monkeypatch):
        """A broken frame source must not make camera_capture unusable."""
        from tools import camera
        monkeypatch.setattr(config, "CAMERA_ENABLED", True, raising=False)
        camera.set_tracker_frame_source(
            lambda: (_ for _ in ()).throw(RuntimeError("tracker died")))

        class _Cap:
            def isOpened(self): return False
        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: _Cap())
        try:
            with pytest.raises(RuntimeError):
                camera.capture()
        finally:
            camera.set_tracker_frame_source(None)


class TestReleaseAndResume:
    def test_release_is_time_boxed(self):
        """Forgetting to resume must not silently end hand tracking, so the
        release expires by itself.

        Asserted as "paused is a deadline, not a latch" rather than by sleeping
        past it: release_for has a one-second floor, so a real wait would make
        this the slowest test in the suite for no extra confidence.
        """
        import time as _t
        t = handtrack.HandTracker.__new__(handtrack.HandTracker)
        t._lock = __import__("threading").Lock()
        t._paused_until = 0.0
        t.release_for(300)
        assert t.paused
        t._paused_until = _t.time() - 1        # the deadline passes
        assert not t.paused, "the pause must expire on its own"

    def test_release_has_a_floor(self):
        """release_for(0) would be a no-op that reported success — the caller
        asked for the camera back and would not get it."""
        t = handtrack.HandTracker.__new__(handtrack.HandTracker)
        t._lock = __import__("threading").Lock()
        t._paused_until = 0.0
        t.release_for(0)
        assert t.paused

    def test_resume_reclaims_it(self):
        t = handtrack.HandTracker.__new__(handtrack.HandTracker)
        t._lock = __import__("threading").Lock()
        t._paused_until = 0.0
        t.release_for(300)
        t.resume()
        assert not t.paused


class TestMissingObservationIsNotStillness:
    """The single most important property the native tracker shares with the
    browser one, for a different reason: there, a frozen tab replays bytes; here,
    a dropped frame or a released camera returns nothing. Both must mean 'no
    observation', never 'a hand held perfectly still'."""

    def test_a_dropped_frame_does_not_extend_a_pinch(self):
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        # A pinch begins…
        for i in range(5):
            rec.feed_cursors([(0.5, 0.5 + i * 0.001, True)], 1000.0 + i * 0.05)
        # …then the camera goes away for well past PINCH_HOLD_SECONDS.
        fired = []
        for i in range(60):
            fired += rec.feed_cursors(None, 1000.3 + i * 0.05)
        assert "pinch_hold" not in fired

    def test_a_dead_camera_does_not_claim_the_hands_left(self):
        """`[]` and `None` must NOT behave the same, and the difference is a
        factual claim about the user.

        `describe("hands_gone")` writes "hands left the camera" into the
        awareness log — durably, into perception_log, where the proactive
        reviewer reads it. When the camera dies mid-gesture that sentence is
        simply false: the hands may still be right there. An earlier version of
        this test asserted both cases emitted hands_gone, which is exactly the
        behaviour that is wrong, and it passed against an implementation whose
        two branches were identical. It proved nothing.
        """
        rec = gestures.GestureRecognizer(cooldown_seconds=0)

        # Observed absence: the source is fine and the hands went down.
        rec.feed_cursors([(0.5, 0.5, False)], 1000.0)
        assert rec.feed_cursors([], 1000.1) == ["hands_gone"]

        # No observation at all: say nothing about the hands.
        rec.feed_cursors([(0.5, 0.5, False)], 1000.2)
        assert rec.feed_cursors(None, 1000.3) == [], \
            "a dead camera must not report that the hands left"

    def test_hands_returning_after_a_dead_camera_re_announce(self):
        """The silence must not become a latch — if `_announced_present` stayed
        true, the hands coming back would go unnoticed for ever."""
        rec = gestures.GestureRecognizer(cooldown_seconds=0)
        rec.feed_cursors([(0.5, 0.5, False)], 1000.0)
        rec.feed_cursors(None, 1000.1)
        assert rec.feed_cursors([(0.5, 0.5, False)], 1000.2) == ["hands_present"]
