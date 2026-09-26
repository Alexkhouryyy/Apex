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
import pathlib
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


def _open_hand(*, openness=1.0, span=0.15):
    """21 landmarks with every finger placed, so is_open_palm has real PIP/tip
    pairs to compare rather than the four points `_hand` above bothers with.

    `openness` interpolates each fingertip from folded-in-at-the-PIP (0.0) to
    fully extended (1.0) — the thing is_open_palm actually measures, expressed
    directly rather than as a pose a reader has to visualize. Scaled by `span`
    itself (not a fixed constant) because `is_open_palm`'s margin is a fraction
    of span too — a fixed pixel-ish range would make the mapping between
    `openness` and the production margin arbitrary instead of proportional.
    """
    wrist_y = 0.5 + span
    lms = [_lm(0.5, wrist_y) for _ in range(21)]
    lms[handtrack.WRIST] = _lm(0.5, wrist_y)
    lms[handtrack.MIDDLE_MCP] = _lm(0.5, 0.5)
    pip_y = 0.45
    tip_y = pip_y - openness * span
    lms[handtrack.INDEX_PIP] = _lm(0.47, pip_y)
    lms[handtrack.INDEX_TIP] = _lm(0.47, tip_y)
    lms[handtrack.MIDDLE_PIP] = _lm(0.50, pip_y)
    lms[handtrack.MIDDLE_TIP] = _lm(0.50, tip_y)
    lms[handtrack.RING_PIP] = _lm(0.53, pip_y)
    lms[handtrack.RING_TIP] = _lm(0.53, tip_y)
    lms[handtrack.PINKY_PIP] = _lm(0.56, pip_y)
    lms[handtrack.PINKY_TIP] = _lm(0.56, tip_y)
    lms[handtrack.THUMB_TIP] = _lm(0.35, 0.5)
    return lms


class TestIsOpenPalm:
    """The board's cancel gesture. Must fire on a deliberate flat hand and
    nowhere else — a false positive here cancels a drag the user never meant
    to abandon; a false negative leaves no escape hatch at all."""

    def test_a_fully_open_hand_is_open(self):
        assert handtrack.is_open_palm(_open_hand(openness=1.0)) is True

    def test_a_closed_fist_is_not_open(self):
        assert handtrack.is_open_palm(_open_hand(openness=0.0)) is False

    def test_a_half_curled_hand_is_not_open(self):
        """THE margin this function exists for. A tip merely past its own PIP
        by tracking noise must not register as a deliberate open hand — that
        would make ordinary hand wobble fire the cancel gesture at random."""
        assert handtrack.is_open_palm(_open_hand(openness=0.1)) is False

    @pytest.mark.parametrize("finger_tip", [
        "index_tip", "middle_tip", "ring_tip", "pinky_tip",
    ])
    def test_one_curled_finger_is_enough_to_refuse(self, finger_tip):
        """Pointing (one finger extended, the rest folded) must never read as
        'open' — only a fully splayed hand may cancel a grab. Here it is the
        OTHER three that are open and just one folded, the complementary case:
        a single folded finger among four extended ones must still refuse."""
        lms = _open_hand(openness=1.0)
        tip_idx = getattr(handtrack, finger_tip.upper())
        pip_idx = getattr(handtrack, finger_tip.upper().replace("TIP", "PIP"))
        # Fold just this one finger back onto its own PIP; the other three
        # stay fully extended from the openness=1.0 baseline.
        lms[tip_idx] = _lm(lms[pip_idx].x, lms[pip_idx].y)
        assert handtrack.is_open_palm(lms) is False

    def test_a_degenerate_span_does_not_divide_by_zero(self):
        h = _open_hand(openness=1.0)
        h[handtrack.WRIST] = h[handtrack.MIDDLE_MCP]
        assert handtrack.is_open_palm(h) is None

    @pytest.mark.parametrize("bad", [[], None, "not landmarks", [_lm(0, 0)]])
    def test_garbage_landmarks_return_none_not_raise(self, bad):
        assert handtrack.is_open_palm(bad) is None

    def test_none_is_distinct_from_false(self):
        """A caller (landmarks_to_cursor) that collapsed None into False would
        be making a claim — 'not open' — about a hand it could not actually
        read. The two must stay different values here even though the caller
        is free to coerce them."""
        garbage_result = handtrack.is_open_palm(None)
        closed_result = handtrack.is_open_palm(_open_hand(openness=0.0))
        assert garbage_result is None
        assert closed_result is False
        assert garbage_result is not closed_result


class TestLandmarksToCursor:
    def test_the_index_fingertip_is_the_cursor(self):
        lms = _hand(x=0.3, y=0.7)
        lms[handtrack.INDEX_TIP] = _lm(0.25, 0.75)
        x, y, _p, _op = handtrack.landmarks_to_cursor(lms, mirror=False)
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
        # Held still first: a hand that is still coming INTO view is not
        # swiping (SWIPE_SETTLE_SECONDS), and this test is about the sign.
        # The thumb is moved well clear so the hand is OPEN throughout — the
        # fixture's default thumb sits where this path starts, which reads as
        # a pinch, and letting go of a pinch is not a swipe.
        for k in range(14):
            lms = _hand()
            lms[handtrack.INDEX_TIP] = _lm(0.9, 0.5)
            lms[handtrack.THUMB_TIP] = _lm(0.5, 0.95)
            rec.feed_cursors([handtrack.landmarks_to_cursor(lms, mirror=True)],
                             999.3 + k * 0.05)
        for i in range(16):
            lms = _hand()
            lms[handtrack.THUMB_TIP] = _lm(0.5, 0.95)
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
        x, y, _, _op = handtrack.landmarks_to_cursor(lms, mirror=False)
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


class TestBrokenOpenCVInstall:
    """A half-deleted cv2 imports fine and has no VideoCapture.

    This happened for real on 2026-08-23. The conflict warning said "keep
    opencv-contrib-python and uninstall the others" — following it deleted files
    contrib still needed, because every opencv-* wheel writes into the SAME cv2
    directory and pip uninstalls by the removed package's manifest. The advice
    broke the install it was protecting, and the symptom was an AttributeError
    from inside a camera loop, which reads like a code bug.
    """

    def test_a_gutted_cv2_is_diagnosed_not_left_to_crash(self, monkeypatch):
        import sys
        import types as _t
        stub = _t.ModuleType("cv2")          # imports fine, has nothing
        monkeypatch.setitem(sys.modules, "cv2", stub)
        ok, why = handtrack.available()
        assert ok is False
        assert "VideoCapture" in why
        assert "uninstall" in why.lower(), "must say how to repair it"

    def test_the_repair_removes_every_opencv_before_installing_one(self):
        """The load-bearing property. Removing only the loser is what broke it;
        the fix has to name all four packages."""
        cmd = handtrack.opencv_repair_command()
        for pkg in handtrack._OPENCV_DISTS:
            assert pkg in cmd, f"{pkg} must be uninstalled too"
        assert cmd.index("uninstall") < cmd.index("install opencv-contrib"), \
            "uninstall must come before the reinstall"

    def test_the_conflict_warning_no_longer_advises_the_destructive_fix(self):
        """The old wording is the bug. Assert it cannot come back."""
        import inspect
        src = inspect.getsource(handtrack.HandTracker.run)
        assert "uninstall the others" not in src.lower()
        assert "do not just uninstall" in src.lower()


class TestLatestJpeg:
    """The board's video backdrop. Found live: hand tracking (cursors, pinch)
    runs through cv2.VideoCapture + MediaPipe, a path that does not touch this
    function at all — so this can fail on its own, invisibly, while gestures
    keep working perfectly. Before this file, it did: any exception here was
    swallowed and returned None forever, which is byte-for-byte what "camera
    not delivering frames" looks like from the board. The two are not the same
    problem and must not print the same way — one means hand tracking is
    dead, the other means only the picture is.
    """

    def _tracker(self):
        t = handtrack.HandTracker(log=None)
        return t

    def test_a_working_frame_encodes_to_real_jpeg_bytes(self):
        import numpy as np
        t = self._tracker()
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        t._latest_frame = frame
        t._latest_ts = __import__("time").time()
        out = t.latest_jpeg()
        assert out is not None and out[:2] == b"\xff\xd8", "not a real JPEG"

    @pytest.mark.parametrize("mirror", [True, False])
    def test_the_picture_is_mirrored_exactly_when_the_hands_are(self, monkeypatch, mirror):
        # A hand at the RIGHT of the raw camera frame is on the user's LEFT.
        # Mirrored hands put its ring on the left of the board, so the picture
        # behind it must show it on the left too, or everything feels reversed.
        import cv2
        import numpy as np
        monkeypatch.setattr(config, "HANDTRACK_MIRROR", mirror, raising=False)
        t = self._tracker()
        frame = np.zeros((40, 80, 3), dtype=np.uint8)
        frame[:, 60:] = 255                          # bright on the raw frame's right
        t._latest_frame, t._latest_ts = frame, __import__("time").time()
        img = cv2.imdecode(np.frombuffer(t.latest_jpeg(), np.uint8), cv2.IMREAD_GRAYSCALE)
        left, right = img[:, :20].mean(), img[:, 60:].mean()
        assert (left > right) == mirror
        # The ring agrees: a raw x of 0.9 lands at 0.1 when mirrored.
        lms = [type("P", (), {"x": 0.9, "y": 0.5, "z": 0.0})() for _ in range(21)]
        cur = handtrack.landmarks_to_cursor(lms, mirror=mirror)
        assert (cur[0] < 0.5) == mirror
        assert t.latest_frame()[:, 60:].mean() == 255, "the camera tool keeps the raw frame"

    def test_no_frame_yet_returns_none_quietly(self):
        """Nothing has been captured — not a failure, just not there yet."""
        t = self._tracker()
        assert t.latest_jpeg() is None

    def test_an_encode_exception_is_reported_not_swallowed(self, monkeypatch, capsys):
        import numpy as np
        t = self._tracker()
        t._latest_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        t._latest_ts = __import__("time").time()

        def _raise(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr("cv2.imencode", _raise)

        assert t.latest_jpeg() is None
        out = capsys.readouterr().out
        assert "boom" in out, "the actual exception must be visible, not hidden"
        assert "gestures" in out.lower() or "hand tracking itself" in out.lower(), \
            "must say this is NOT the same as hand tracking being broken"

    def test_the_failure_is_reported_once_not_every_frame(self, monkeypatch, capsys):
        """At ~15-20 Hz, printing on every frame would flood the console into
        uselessness within seconds — worse than the silence it replaces."""
        import numpy as np
        t = self._tracker()
        t._latest_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        t._latest_ts = __import__("time").time()
        monkeypatch.setattr("cv2.imencode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

        for _ in range(5):
            assert t.latest_jpeg() is None
        assert capsys.readouterr().out.count("[HandTrack]") == 1

    def test_ok_false_with_no_exception_is_also_reported(self, monkeypatch, capsys):
        """cv2.imencode signals failure by returning ok=False, not only by
        raising — both must be caught, or this silently returns None only for
        the exception case and stays a mystery for the other."""
        import numpy as np
        t = self._tracker()
        t._latest_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        t._latest_ts = __import__("time").time()
        monkeypatch.setattr("cv2.imencode", lambda *a, **k: (False, None))

        assert t.latest_jpeg() is None
        assert "[HandTrack]" in capsys.readouterr().out

    def test_a_known_opencv_conflict_is_named_as_the_likely_cause(self, monkeypatch, capsys):
        import numpy as np
        t = self._tracker()
        t._latest_frame = np.zeros((10, 10, 3), dtype=np.uint8)
        t._latest_ts = __import__("time").time()
        monkeypatch.setattr(handtrack, "opencv_conflict",
                            lambda: ["opencv-python", "opencv-contrib-python"])
        monkeypatch.setattr("cv2.imencode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))

        t.latest_jpeg()
        out = capsys.readouterr().out
        assert "opencv-python" in out and "uninstall" in out.lower()


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


class TestDelegateChoice:
    """Which processor the model runs on, and saying so.

    Measured: 17.6 ms/frame on CPU, ~35% of one core at 20 Hz. Worth moving to
    GPU. Also verified that MediaPipe RAISES when a GPU context cannot be
    created (`RuntimeError: Service "kGpuService" ...`) rather than degrading
    quietly, which is what makes a fallback safe to write.
    """

    def _fake(self, gpu_works: bool):
        calls = []

        def try_create(delegate):
            calls.append(delegate)
            if delegate == "GPU" and not gpu_works:
                raise RuntimeError('Service "kGpuService", required by node ...')
            return f"landmarker-on-{delegate}"
        return try_create, calls

    def test_gpu_is_used_when_it_works(self):
        try_create, calls = self._fake(gpu_works=True)
        obj, used, note = handtrack.choose_delegate("auto", try_create)
        assert used == "GPU" and obj == "landmarker-on-GPU"
        assert calls == ["GPU"], "must not build a CPU one it does not need"
        assert note == ""

    def test_it_falls_back_rather_than_leaving_you_with_nothing(self):
        try_create, calls = self._fake(gpu_works=False)
        obj, used, note = handtrack.choose_delegate("auto", try_create)
        assert used == "CPU" and obj == "landmarker-on-CPU"
        assert calls == ["GPU", "CPU"]
        assert "GPU unavailable" in note

    def test_an_explicit_gpu_request_that_fails_is_louder(self):
        """Asking for GPU and silently getting CPU is the fail-open shape: you
        would believe you were on GPU for ever. `auto` settling is unremarkable;
        an unhonoured explicit request is not."""
        try_create, _ = self._fake(gpu_works=False)
        _obj, used, note = handtrack.choose_delegate("gpu", try_create)
        assert used == "CPU"
        assert "requested" in note.lower()

    def test_cpu_never_touches_the_gpu(self):
        try_create, calls = self._fake(gpu_works=True)
        _obj, used, _note = handtrack.choose_delegate("cpu", try_create)
        assert used == "CPU" and calls == ["CPU"]

    @pytest.mark.parametrize("junk", ["", None, "Metal", "  AUTO  ", "cuda"])
    def test_an_unknown_preference_behaves_as_auto(self, junk):
        """A typo in .env must not disable hand tracking."""
        try_create, _ = self._fake(gpu_works=True)
        _obj, used, _note = handtrack.choose_delegate(junk, try_create)
        assert used == "GPU"

    def test_the_delegate_actually_used_is_always_returned(self):
        """The whole reason this returns `used` rather than just the object:
        the caller has to be able to state it instead of assuming."""
        for works in (True, False):
            try_create, _ = self._fake(gpu_works=works)
            _obj, used, _n = handtrack.choose_delegate("auto", try_create)
            assert used in ("GPU", "CPU")

    def test_the_tracker_reports_the_delegate_it_got(self):
        import inspect
        src = inspect.getsource(handtrack.HandTracker._open)
        assert "Inference on" in src, "a silent delegate is an assumed delegate"


class TestDetectionConfidence:
    def test_it_stays_inside_the_band_where_mediapipe_is_usable(self):
        """Below ~0.3 MediaPipe reports hand-shaped clutter as hands; above
        ~0.75 it starts missing real ones. The number inside that band is a
        measurement (see config.py), but the band itself is a property of the
        model and a value outside it is a mistake, not a tuning choice."""
        assert 0.3 <= handtrack.DEFAULT_MIN_CONFIDENCE <= 0.75

    def test_it_is_configurable(self):
        import inspect
        src = inspect.getsource(handtrack.build_landmarker)
        assert "HANDTRACK_MIN_CONFIDENCE" in src


class TestTheFallbacksAgreeWithConfig:
    """`handtrack` keeps module-level defaults for the two tuning numbers and
    reads them via `getattr(config, ..., DEFAULT)`. That fallback never fires in
    practice, because config always defines both — which is exactly why it can
    drift for months without anyone noticing.

    It has already happened once. `HANDTRACK_PINCH_RATIO` was raised from 0.45
    to 0.70 and `HANDTRACK_MIN_CONFIDENCE` lowered from 0.7 to 0.5 after a real
    calibration run, and both module constants kept the pre-measurement values —
    two quiet second answers disagreeing with the first, one of them by a
    quarter. These tests exist so the next change to either number cannot leave
    a stale twin behind."""

    @staticmethod
    def _shipped_default(name: str) -> float:
        """Read the literal out of config.py's source rather than
        `getattr(config, name)`. The attribute is whatever the environment
        overrode it to, so comparing against it would make these tests pass or
        fail based on the developer's own `.env` — which is how an
        environment-dependent test got into CI on this project once already.
        The claim here is about the number Apex *ships*."""
        import re
        src = (pathlib.Path(config.__file__)).read_text(encoding="utf-8")
        m = re.search(rf'{name} = float\(os\.getenv\("{name}", "([0-9.]+)"\)\)', src)
        assert m, f"could not find the shipped default for {name} in config.py"
        return float(m.group(1))

    def test_the_pinch_fallback_matches_the_shipped_default(self):
        assert handtrack.DEFAULT_PINCH_RATIO == self._shipped_default(
            "HANDTRACK_PINCH_RATIO")

    def test_the_confidence_fallback_matches_the_shipped_default(self):
        assert handtrack.DEFAULT_MIN_CONFIDENCE == self._shipped_default(
            "HANDTRACK_MIN_CONFIDENCE")


class TestCameraOpenBackoff:
    """`_open()` is called from `_tick()`, which runs at HANDTRACK_POLL_HZ.

    Before the backoff, a machine with no camera — or one whose camera was busy
    in a video call — rebuilt a `cv2.VideoCapture` every 50 ms for as long as
    Apex ran. Each attempt is a full V4L2 + FFMPEG device enumeration, so it
    burned real CPU at 20 Hz and wrote several lines of OpenCV C++ stderr per
    attempt. `_say()` deduplicated the Python status line, which is exactly why
    this stayed invisible from above while the log filled from below.

    It also had a second cost that was harder to see: on a CI runner the
    tracker's spin starved the turn it was booted alongside, and the smoke
    check that watches a tool call take effect failed intermittently. A busy
    loop does not only waste a machine, it changes the timing of everything
    sharing it.
    """

    @staticmethod
    def _tracker(monkeypatch, opens: list, *, succeed: bool = False):
        t = handtrack.HandTracker(log=None)

        class _Cap:
            def isOpened(self):
                opens.append(True)
                return succeed
            def read(self):
                return False, None
            def release(self):
                pass

        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: _Cap())
        return t

    def test_a_refused_camera_is_not_retried_on_the_very_next_tick(self, monkeypatch):
        opens = []
        t = self._tracker(monkeypatch, opens)
        assert t._open(now=100.0) is False
        assert len(opens) == 1
        # 50 ms later — the next tick at 20 Hz.
        assert t._open(now=100.05) is False
        assert len(opens) == 1, \
            "a second device open inside the backoff window is the bug itself"

    def test_it_does_retry_once_the_window_passes(self, monkeypatch):
        """A backoff that becomes 'never' is a different bug: plug a camera in
        and tracking would stay dead until restart."""
        opens = []
        t = self._tracker(monkeypatch, opens)
        t._open(now=100.0)
        assert t._open(now=100.0 + handtrack.CAMERA_RETRY_FIRST + 0.01) is False
        assert len(opens) == 2

    def test_the_wait_grows_and_then_stops_growing(self, monkeypatch):
        opens = []
        t = self._tracker(monkeypatch, opens)
        now, seen = 100.0, []
        for _ in range(12):
            t._open(now=now)
            seen.append(t._retry_delay)
            now = t._retry_at + 0.001
        assert seen[0] < seen[1] < seen[2], "the wait must grow after repeats"
        assert max(seen) == handtrack.CAMERA_RETRY_MAX, \
            "and must cap, or a long-running Apex would wait hours"

    def test_a_camera_that_opens_clears_the_backoff(self, monkeypatch):
        """A camera freed after a video call must not inherit the wait built up
        while it was busy."""
        opens = []
        t = self._tracker(monkeypatch, opens)
        t._open(now=100.0)
        t._open(now=200.0)
        assert t._retry_delay > handtrack.CAMERA_RETRY_FIRST

        class _Open:
            def isOpened(self): return True
            def read(self): return False, None
            def release(self): pass
        monkeypatch.setattr("cv2.VideoCapture", lambda *a, **k: _Open())
        monkeypatch.setattr(handtrack, "build_landmarker",
                            lambda **k: (object(), "CPU", ""))
        t._open(now=t._retry_at + 0.001)
        assert t._retry_at == 0.0
        assert t._retry_delay == handtrack.CAMERA_RETRY_FIRST

    def test_no_camera_is_reported_without_downloading_a_model(self, monkeypatch, capsys):
        opens = []
        t = self._tracker(monkeypatch, opens)
        monkeypatch.setattr(handtrack, "available", lambda: (True, ""))
        monkeypatch.setattr(handtrack, "opencv_conflict", lambda: [])
        def forbidden(*args, **kwargs):
            raise AssertionError("A missing camera must not need a model download")
        monkeypatch.setattr(handtrack, "ensure_model", forbidden)
        def one_tick(now):
            t._open(now)
            t._stop.set()
        monkeypatch.setattr(t, "_tick", one_tick)
        t.run()
        output = capsys.readouterr().out
        assert opens and "Watching camera" in output and "would not open" in output

    def test_model_failure_releases_camera_and_backs_off(self, monkeypatch):
        opens = []
        t = self._tracker(monkeypatch, opens)
        released = []
        class Camera:
            def isOpened(self): return True
            def release(self): released.append(True)
        monkeypatch.setattr("cv2.VideoCapture", lambda *args: Camera())
        def broken(**kwargs):
            raise RuntimeError("download unavailable")
        monkeypatch.setattr(handtrack, "build_landmarker", broken)
        assert t._open(now=100) is False
        assert released and t._cap is None
        assert t._retry_at == pytest.approx(100 + handtrack.CAMERA_RETRY_MAX, abs=.1)

    def test_resume_clears_the_backoff_too(self, monkeypatch):
        """`release_camera` then `resume` is a person explicitly asking for the
        camera back. Making them wait out a 30-second backoff would read as the
        resume having failed."""
        opens = []
        t = self._tracker(monkeypatch, opens)
        t._open(now=100.0)
        t._open(now=200.0)
        assert t._retry_at > 0.0
        t.resume()
        assert t._retry_at == 0.0
        assert t._retry_delay == handtrack.CAMERA_RETRY_FIRST


class TestSmoothing:
    """The One-Euro filter on hand positions: steady when still, not late when fast."""

    def test_a_still_hand_stops_shivering(self):
        import random
        f = handtrack.OneEuro(handtrack.SMOOTH_MIN_CUTOFF, handtrack.SMOOTH_BETA)
        r = random.Random(3)
        raw = [0.5 + r.uniform(-0.006, 0.006) for _ in range(120)]      # MediaPipe-sized jitter
        out = [f(x, i / 30) for i, x in enumerate(raw)]
        spread = lambda v: max(v) - min(v)
        assert spread(out[30:]) < spread(raw[30:]) / 2.5, "held still, the cursor must be steady"

    def test_a_fast_move_is_not_left_behind(self):
        f = handtrack.OneEuro(handtrack.SMOOTH_MIN_CUTOFF, handtrack.SMOOTH_BETA)
        t, x = 0.0, 0.2
        for _ in range(10):
            f(x, t); t += 1 / 30
        for _ in range(12):                       # 2 screen-widths a second
            x += 2 / 30; t += 1 / 30
            y = f(x, t)
        assert abs(x - y) < 0.03, f"lagging {abs(x - y):.3f} of the screen behind a quick move"

    def test_each_hand_is_smoothed_on_its_own(self):
        from types import SimpleNamespace as NS
        t = handtrack.HandTracker.__new__(handtrack.HandTracker)
        t._lock = __import__("threading").Lock()
        def hand(x):
            pts = [NS(x=0.5, y=0.5) for _ in range(21)]
            pts[handtrack.INDEX_TIP] = NS(x=x, y=0.5)
            pts[handtrack.WRIST] = NS(x=x, y=0.7); pts[handtrack.MIDDLE_MCP] = NS(x=x, y=0.5)
            pts[handtrack.THUMB_TIP] = NS(x=x + 0.2, y=0.5)
            return pts
        res = lambda *xs: NS(hand_landmarks=[hand(x) for x in xs], handedness=[])
        for i in range(10):
            t._read_hands(res(0.3), now=i / 30)
        cursors, _ = t._read_hands(res(0.3, 0.8), now=10 / 30)
        xs = sorted(c[0] for c in cursors)
        # Mirrored: 0.8 on the camera image is 0.2 on the board.
        assert abs(xs[0] - 0.2) < 1e-9, "a new hand starts where it is, not dragged from the other"


def test_detector_work_counts_toward_frame_budget(monkeypatch):
    """A 12 ms detector must not turn a 30 Hz request into a 45 ms loop."""
    clock = [100.0]
    starts, waits = [], []
    work = iter([.012, .050, .009])
    t = handtrack.HandTracker(None, poll_hz=30)
    class Stop:
        def is_set(self): return len(waits) == 3
        def wait(self, timeout=0): waits.append(timeout); clock[0] += timeout
    t._stop = Stop()
    monkeypatch.setattr(handtrack, 'available', lambda: (True, ''))
    monkeypatch.setattr(handtrack, 'opencv_conflict', lambda: [])
    monkeypatch.setattr(handtrack.time, 'monotonic', lambda: clock[0])
    def tick(now): starts.append(clock[0]); clock[0] += next(work)
    monkeypatch.setattr(t, '_tick', tick)
    t.run()
    assert waits == pytest.approx([1/30-.012, 0, 1/30-.009])
    assert starts[1]-starts[0] == pytest.approx(1/30)
    assert starts[2]-starts[1] == pytest.approx(.050)


def test_study_freshness_includes_capture_and_inference(monkeypatch):
    import numpy as np
    clock = [100.0]
    timestamps = []
    t = handtrack.HandTracker(None)
    class Cap:
        def read(self): clock[0] += .010; return True, np.zeros((4,4,3), dtype=np.uint8)
    class Detector:
        def detect_for_video(self, image, timestamp):
            timestamps.append(timestamp); clock[0] += .100
            return types.SimpleNamespace(hand_landmarks=[])
    t._cap, t._landmarker = Cap(), Detector()
    t._mp = types.SimpleNamespace(Image=lambda **kw: kw, ImageFormat=types.SimpleNamespace(SRGB=1))
    monkeypatch.setattr(t, '_open', lambda now: True)
    monkeypatch.setattr(handtrack.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(config, 'BOARD_ENABLED', False)
    monkeypatch.setattr(t.recognizer, 'feed_cursors', lambda *a: [])
    from agent import gesture_recorder
    monkeypatch.setattr(gesture_recorder, 'observe', lambda *a: None)
    t._tick(200.0)
    assert t.study_sample()['age_ms'] == 110
    t._tick(200.1)
    assert timestamps == [100000, 100110]


def test_finger_feedback_matches_mirroring_and_rejects_invalid_points():
    h = _hand(x=.3, y=.4)
    assert handtrack.fingertip_positions(h)['index'] == [.7,.4]
    assert handtrack.fingertip_positions(h, mirror=False)['index'] == [.3,.4]
    h[handtrack.THUMB_TIP] = _lm(float('nan'),.5)
    h[handtrack.PINKY_TIP] = _lm(2,.5)
    points = handtrack.fingertip_positions(h)
    assert 'thumb' not in points and 'pinky' not in points
    assert handtrack.fingertip_positions(None) == {}
