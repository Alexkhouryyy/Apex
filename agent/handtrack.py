"""Apex's own hand tracking — MediaPipe on the webcam, no browser involved.

The other source of hands, `agent/barehands_watcher.py`, polls a `barehands`
server whose MediaPipe runs inside a Chrome tab. That works, but it means hand
tracking only exists while a browser tab is open **and in front**: browsers pause
`requestAnimationFrame` on a backgrounded tab, the tracker freezes, and Apex goes
blind. The entire staleness machinery over there — `BAREHANDS_STALE_SECONDS`, the
byte-comparison, the "stage is frozen" state — exists only to detect that.

Owning the capture deletes that whole failure class. This module reads the camera
directly, so hand tracking works headless, in resident mode, with no browser
running at all.

Both sources feed the same `agent/gestures.GestureRecognizer`, which never knew
where the numbers came from. Adding this cost the recognizer nothing.

## Licensing

MediaPipe is Apache 2.0 and `mediapipe-models` are Google's, so this is an
ordinary dependency. Nothing here is derived from `barehands`: the geometry below
comes from MediaPipe's documented 21-point hand model, and the thresholds are
Apex's own, arrived at independently and tunable because a threshold fitted to
one person's hand is not a constant.

## Two things worth knowing before reading the code

**The webcam is exclusive.** While this holds the device, nothing else on the
machine can open it — not Zoom, not Teams, and not Apex's own `camera_capture`
tool. That last one would be a self-inflicted wound, so `latest_frame()` exists:
when the tracker is running, `tools/camera.py` takes a frame from it instead of
fighting for the device. `release_for()` hands the camera back on demand.

**Handedness is real identity.** MediaPipe reports Left/Right with a confidence
score. The browser path could not see this and had to infer identity purely from
proximity; here, `handedness` pairs hands directly, which is strictly better when
two hands cross or one leaves.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from agent.awareness import AwarenessLog

# MediaPipe's documented 21-point hand model. Named rather than inlined because
# `lms[8]` in the middle of a geometry expression is unreviewable.
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
MIDDLE_MCP = 9          # base knuckle of the middle finger

# Pinch is the ratio of thumb-to-index distance against the hand's own span
# (wrist to middle knuckle), NOT a pixel distance. A hand near the camera is
# bigger in pixels but not more pinched, so any absolute threshold would make
# pinch depend on how far away you sit.
#
# 0.45 is a starting point, not a measurement. It was chosen without a camera to
# test against, so it is configurable and HANDTRACK_DEBUG logs the ratio it
# actually sees — tune it against your hand rather than trusting this number.
DEFAULT_PINCH_RATIO = 0.45

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"


def model_path() -> Path:
    """Where the MediaPipe model lives, downloaded on first use."""
    base = Path(os.path.expanduser("~/.apex/models"))
    return base / MODEL_NAME


def available() -> tuple[bool, str]:
    """(usable, why-not). Reports which piece is missing rather than one
    unhelpful False — the two failure modes need different fixes."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, "opencv is not installed (pip install opencv-python)"
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return False, "mediapipe is not installed (pip install mediapipe)"
    return True, ""


def ensure_model(timeout: float = 120.0) -> Optional[Path]:
    """Fetch the hand landmarker model if it isn't already on disk.

    Returns the path, or None if it could not be obtained. Downloads to a
    temporary name and renames, so an interrupted download can never leave a
    truncated file that loads as a corrupt model.
    """
    p = model_path()
    if p.is_file() and p.stat().st_size > 1_000_000:
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".partial")
        print(f"[HandTrack] Downloading the hand model (~7.5 MB) to {p} …")
        with urllib.request.urlopen(MODEL_URL, timeout=timeout) as r, \
                open(tmp, "wb") as f:
            f.write(r.read())
        tmp.replace(p)
        print("[HandTrack] Model ready.")
        return p
    except Exception as e:
        print(f"[HandTrack] Could not download the hand model: {e}")
        return None


def pinch_ratio(lms) -> Optional[float]:
    """Thumb-to-index distance as a fraction of the hand's own span.

    Scale-invariant by construction, which is what makes one threshold work at
    arm's length and up close. Returns None if the landmarks are unusable —
    including a degenerate span, which would otherwise divide by zero and take
    the tracker thread down with it.
    """
    try:
        t, i = lms[THUMB_TIP], lms[INDEX_TIP]
        w, m = lms[WRIST], lms[MIDDLE_MCP]
    except (IndexError, TypeError):
        return None
    try:
        gap = ((t.x - i.x) ** 2 + (t.y - i.y) ** 2) ** 0.5
        span = ((w.x - m.x) ** 2 + (w.y - m.y) ** 2) ** 0.5
    except (AttributeError, TypeError):
        return None
    if span <= 1e-6:
        return None
    return gap / span


def landmarks_to_cursor(lms, *, mirror: bool = True,
                        threshold: Optional[float] = None):
    """One hand's 21 landmarks -> `(x, y, pinched)` for the recognizer.

    The index fingertip is the cursor, because a pointing finger is where a
    person believes they are pointing.

    `mirror` flips x into selfie space. A raw webcam frame is not mirrored, so
    without this a hand moving to the user's right travels *left* in the image
    and `swipe_right` fires for a leftward wave — an inverted axis that would be
    maddening to debug and trivially avoidable here.
    """
    if threshold is None:
        threshold = getattr(config, "HANDTRACK_PINCH_RATIO", DEFAULT_PINCH_RATIO)
    try:
        tip = lms[INDEX_TIP]
        x, y = float(tip.x), float(tip.y)
    except (IndexError, AttributeError, TypeError, ValueError):
        return None
    ratio = pinch_ratio(lms)
    pinched = ratio is not None and ratio < threshold
    if mirror:
        x = 1.0 - x
    # MediaPipe normalizes to the frame, but a hand at the very edge can report
    # slightly outside it. Clamp so window fractions stay window fractions.
    x = min(1.0, max(0.0, x))
    y = min(1.0, max(0.0, y))
    return (x, y, pinched)


def order_by_handedness(cursors: list, labels: list) -> list:
    """Put hands in a stable order using MediaPipe's Left/Right call.

    Detection order carries no identity and can renumber between frames, which
    is what makes a second hand appear as a screen-wide swipe. The recognizer
    defends against that with proximity pairing, but handedness is better
    evidence than proximity and it costs nothing: sorting by label means the
    left hand stays in the same slot even when it briefly leaves and returns.

    Falls back to the given order when labels are missing or ambiguous — the
    recognizer's proximity pairing still covers that case.
    """
    if not labels or len(labels) != len(cursors):
        return cursors
    try:
        keyed = sorted(zip(labels, cursors), key=lambda pair: str(pair[0]))
    except Exception:
        return cursors
    return [c for _lab, c in keyed]


class HandTracker(threading.Thread):
    """Reads the webcam, finds hands, feeds gestures into the awareness log.

    Mirrors `agent/iot_watcher.IoTWatcher`: daemon thread, `threading.Event` for
    both sleep and stop, everything that decides anything living in a pure
    function this class merely calls.
    """

    def __init__(self, log: "AwarenessLog", *, on_gesture=None,
                 device_index: Optional[int] = None,
                 poll_hz: Optional[float] = None):
        super().__init__(daemon=True, name="HandTracker")
        self.log = log
        self.on_gesture = on_gesture
        self.device_index = (
            device_index if device_index is not None
            else getattr(config, "CAMERA_DEVICE_INDEX", 0)
        )
        hz = poll_hz or getattr(config, "HANDTRACK_POLL_HZ", 20.0)
        self.interval = 1.0 / max(1.0, float(hz))

        from agent.gestures import GestureRecognizer
        self.recognizer = GestureRecognizer()

        self._stop = threading.Event()
        self._paused_until = 0.0
        self._lock = threading.Lock()
        self._latest_frame = None       # BGR ndarray, for tools/camera.py
        self._latest_ts = 0.0
        self._cap = None
        self._landmarker = None
        self._frame_no = 0
        self._reported = ""

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def release_for(self, seconds: float = 60.0) -> str:
        """Hand the webcam back to the rest of the machine for a while.

        The camera is exclusive: while Apex holds it, a video call cannot open
        it, and the failure surfaces as "your camera is broken" rather than
        "something else is using it". This is the escape hatch, and it is time
        boxed so forgetting to resume does not silently end hand tracking.
        """
        with self._lock:
            self._paused_until = time.time() + max(1.0, float(seconds))
        return (f"[HandTrack] Camera released for {int(seconds)}s — hand "
                f"tracking is paused until then.")

    def resume(self) -> str:
        with self._lock:
            self._paused_until = 0.0
        return "[HandTrack] Camera reclaimed — hand tracking resumes."

    @property
    def paused(self) -> bool:
        return time.time() < self._paused_until

    def latest_frame(self):
        """The most recent camera frame, or None.

        `tools/camera.py` reads this instead of opening the device, because the
        tracker is already holding it. Without this, turning on hand tracking
        would silently break Apex's own `camera_capture` tool — two parts of the
        same program fighting over one device.
        """
        with self._lock:
            if self._latest_frame is None:
                return None
            if time.time() - self._latest_ts > 5.0:
                return None
            return self._latest_frame.copy()

    # -- the loop ----------------------------------------------------------
    def run(self) -> None:
        ok, why = available()
        if not ok:
            print(f"[HandTrack] Hand tracking is off: {why}")
            self._stop.wait()          # park; never spin
            return
        if ensure_model() is None:
            print("[HandTrack] Hand tracking is off: no model file.")
            self._stop.wait()
            return

        print(f"[HandTrack] Watching camera {self.device_index} at "
              f"{1 / self.interval:.0f} Hz.")
        try:
            while not self._stop.wait(timeout=self.interval):
                try:
                    self._tick(time.time())
                except Exception as e:
                    # A raise here would end hand tracking for the rest of the
                    # session with nothing but a dead thread to show for it.
                    print(f"[HandTrack] tick error: {e}")
        finally:
            self._teardown()

    def _teardown(self) -> None:
        cap, self._cap = self._cap, None
        lm, self._landmarker = self._landmarker, None
        for obj, how in ((cap, "release"), (lm, "close")):
            if obj is not None:
                try:
                    getattr(obj, how)()
                except Exception:
                    pass

    def _open(self) -> bool:
        import cv2
        if self._cap is None:
            self._cap = cv2.VideoCapture(self.device_index)
            if not self._cap.isOpened():
                self._cap = None
                self._say("camera_busy",
                          f"[HandTrack] Camera {self.device_index} would not "
                          f"open — something else may be using it.")
                return False
            self._say("camera_open", "[HandTrack] Camera open.")
        if self._landmarker is None:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            self._landmarker = vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=str(model_path())),
                    running_mode=vision.RunningMode.VIDEO,
                    num_hands=2,
                )
            )
            self._mp = mp
        return True

    def _say(self, key: str, line: str) -> None:
        """Print a status line only when the status actually changed.

        At 20 Hz, an unconditional print would emit 1200 identical lines a
        minute and bury everything else in the log.
        """
        if self._reported != key:
            self._reported = key
            print(line)

    def _tick(self, now: float) -> None:
        if self.paused:
            if self._cap is not None:
                self._teardown()
                self._say("paused", "[HandTrack] Camera handed back.")
            self.recognizer.feed_cursors(None, now)
            return

        if not self._open():
            self.recognizer.feed_cursors(None, now)
            return

        ok, frame = self._cap.read()
        if not ok or frame is None:
            self._say("no_frame", "[HandTrack] Camera stopped delivering frames.")
            self._teardown()
            # None, not [] — a dropped frame is a missing observation, not an
            # observation that no hands are up. Conflating them lets a dead
            # camera fabricate a held gesture.
            self.recognizer.feed_cursors(None, now)
            return
        self._say("live", "[HandTrack] Hands tracking live.")

        import cv2
        with self._lock:
            self._latest_frame = frame
            self._latest_ts = now

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        # detect_for_video demands strictly increasing millisecond timestamps;
        # a frame counter is monotonic in a way wall-clock is not.
        self._frame_no += 1
        result = self._landmarker.detect_for_video(
            image, int(self._frame_no * self.interval * 1000))

        cursors, labels = [], []
        mirror = getattr(config, "HANDTRACK_MIRROR", True)
        for idx, lms in enumerate(result.hand_landmarks or []):
            cur = landmarks_to_cursor(lms, mirror=mirror)
            if cur is None:
                continue
            cursors.append(cur)
            labels.append(_handedness_label(result, idx))
            if getattr(config, "HANDTRACK_DEBUG", False):
                # The tuning surface. HANDTRACK_PINCH_RATIO was picked without a
                # camera to test against, so print the ratio actually measured
                # and let the number be chosen from data instead of from me.
                r = pinch_ratio(lms)
                shown = f"{r:.3f}" if r is not None else "n/a"
                print(f"[HandTrack] hand={labels[-1] or '?'} x={cur[0]:.3f} "
                      f"y={cur[1]:.3f} pinch_ratio={shown} pinched={cur[2]}")

        cursors = order_by_handedness(cursors, labels)
        for g in self.recognizer.feed_cursors(cursors, now):
            self._dispatch(g)

    def _dispatch(self, gesture: str) -> None:
        from agent import gestures as _g
        # Always logged, whatever the allowlist says: recognition and action are
        # separate gates, so "I waved and nothing happened" stays diagnosable.
        self.log.add("gesture", _g.describe(gesture))
        action = _g.gesture_action(gesture)
        if action and self.on_gesture:
            try:
                self.on_gesture(gesture, action)
            except Exception as e:
                print(f"[HandTrack] gesture handler error: {e}")


def _handedness_label(result, idx: int) -> str:
    """MediaPipe's Left/Right for hand `idx`, or "" when it did not say."""
    try:
        cats = (result.handedness or [])[idx]
        return str(cats[0].category_name)
    except Exception:
        return ""


# The running tracker, so the release_camera tool and the dashboard can reach it
# without threading a reference through every caller. One per process by
# construction — AwarenessMonitor builds exactly one.
_active: Optional[HandTracker] = None


def set_active_tracker(tracker: Optional[HandTracker]) -> None:
    global _active
    _active = tracker


def active_tracker() -> Optional[HandTracker]:
    return _active
