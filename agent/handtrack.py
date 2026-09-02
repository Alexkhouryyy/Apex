"""Apex's own hand tracking — MediaPipe on the webcam, no browser involved.

An earlier version of Apex also polled a `barehands` server whose MediaPipe ran
inside a Chrome tab. That worked, but it meant hand tracking only existed while
a browser tab was open **and in front**: browsers pause `requestAnimationFrame`
on a backgrounded tab, the tracker freezes, and Apex goes blind. It was removed
— it never reliably tracked hands in practice, and owning the capture directly
(this module) deletes that whole failure class along with the need for it: hand
tracking works headless, in resident mode, with no browser running at all.

This feeds `agent/gestures.GestureRecognizer`, which is blind to where its input
comes from — a design that outlived the second source it was built to support.

## Provenance, stated accurately

MediaPipe is Apache 2.0 and its models are Google's, so that is an ordinary
dependency. The removed `barehands` integration was AGPL-3.0, and the honest
account of what this module owes it, for the record:

**No barehands code was ever copied.** Not a line, and none of its `stage.html`
was ever vendored anywhere in Apex.

**Its source was read first, and one choice matches theirs.** barehands computed
its pinch from landmarks 4 and 8 against a span of 0 to 9, and its README stated
the principle — "the gates measure hand *shape* as ratios, not size, so they hold
at any camera distance". `pinch_ratio` below uses the same four landmarks for the
same reason. That convergence is not an accident of independent invention and
should not be described as one: thumb tip, index tip, wrist and middle knuckle
are the obvious four points, and a distance divided by another distance carries
no creative expression to infringe — but the ordering of events was read-then-
write, and a provenance note that says otherwise would be cited with confidence
by whoever read it next.

**What is Apex's own:** every threshold here (demonstrably, since they are
untuned — see `scripts/calibrate_pinch.py`), the recognizer in `agent/gestures.py`
and its architecture, and the gesture set. barehands' own gestures — clap, claw,
throw, the exploded-view scrub — were never reimplemented.

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
INDEX_PIP = 6
MIDDLE_TIP = 12
MIDDLE_PIP = 10
RING_PIP = 14
RING_TIP = 16
PINKY_PIP = 18
PINKY_TIP = 20

# Pinch is the ratio of thumb-to-index distance against the hand's own span
# (wrist to middle knuckle), NOT a pixel distance. A hand near the camera is
# bigger in pixels but not more pinched, so any absolute threshold would make
# pinch depend on how far away you sit.
#
# This is only the fallback for a config module that somehow lacks the setting;
# config.HANDTRACK_PINCH_RATIO is what actually runs. It is kept in step with
# that default deliberately — the earlier 0.45 here was a guess, and once
# config was set from a real calibration run this constant would have been a
# second, quieter answer disagreeing with the first by a quarter.
DEFAULT_PINCH_RATIO = 0.70

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
             "hand_landmarker/float16/1/hand_landmarker.task")
MODEL_NAME = "hand_landmarker.task"


def model_path() -> Path:
    """Where the MediaPipe model lives, downloaded on first use."""
    base = Path(os.path.expanduser("~/.apex/models"))
    return base / MODEL_NAME


# Every distribution that installs a module called `cv2`. Two of these on one
# machine is a supported-by-nobody configuration.
_OPENCV_DISTS = (
    "opencv-python", "opencv-python-headless",
    "opencv-contrib-python", "opencv-contrib-python-headless",
)


def _dists_installed() -> set:
    """Lowercased names of every installed distribution. Its own function so a
    test can substitute one without inventing packages on the machine."""
    from importlib.metadata import distributions
    return {d.metadata["Name"].lower() for d in distributions()
            if d.metadata and d.metadata.get("Name")}


def opencv_conflict() -> list[str]:
    """Which OpenCV distributions are installed, if more than one.

    `pip install mediapipe` pulls `opencv-contrib-python`, while Apex's
    requirements.txt pins `opencv-python-headless`. Both write into the same
    `cv2` package directory, so the second install overwrites files from the
    first and you get a `cv2` that imports fine and is missing pieces of
    whichever one lost. Nothing errors; things just stop working oddly. Worth
    naming out loud rather than debugging from symptoms.
    """
    try:
        found = _dists_installed()
    except Exception:
        # A diagnostic must never be the thing that stops hand tracking.
        return []
    hits = sorted(n for n in _OPENCV_DISTS if n in found)
    return hits if len(hits) > 1 else []


# The only safe way to end up with one OpenCV. Uninstalling the "loser" of two
# is NOT safe — see opencv_repair_command.
_REPAIR = ("pip uninstall -y opencv-python opencv-python-headless "
           "opencv-contrib-python opencv-contrib-python-headless && "
           "pip install opencv-contrib-python")


def opencv_repair_command() -> str:
    """How to fix a broken cv2, as one command.

    Learned the hard way on 2026-08-23. Every opencv-* wheel installs into the
    SAME `cv2/` directory, so a second install overwrites the first's files
    while pip keeps the first's manifest. Uninstalling either one then deletes
    files the survivor still needs, and `import cv2` succeeds while
    `cv2.VideoCapture` is simply gone.

    So "uninstall the other one" — which is what this module used to advise —
    breaks the install it was trying to protect. The only safe sequence is to
    remove them ALL and install exactly one.
    """
    return _REPAIR


def available() -> tuple[bool, str]:
    """(usable, why-not). Reports which piece is missing rather than one
    unhelpful False — the failure modes need different fixes."""
    try:
        import cv2
    except ImportError:
        return False, ("opencv is not installed — " + _REPAIR)
    # A half-deleted cv2 imports fine and is missing its guts. Without this the
    # first symptom is `AttributeError: module 'cv2' has no attribute
    # 'VideoCapture'` from somewhere deep in a camera loop, which reads like a
    # code bug rather than a packaging one.
    for attr in ("VideoCapture", "cvtColor", "COLOR_BGR2RGB"):
        if not hasattr(cv2, attr):
            return False, (f"cv2 is installed but broken — no `{attr}`. Two "
                           f"OpenCV packages overwrote each other and "
                           f"uninstalling one deleted files the other needs. "
                           f"Repair with: " + _REPAIR)
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


# The fallback for a config module lacking the setting; config.HANDTRACK_MIN_
# CONFIDENCE is what actually runs, and this is kept in step with it so the two
# cannot quietly disagree (tests/test_handtrack.py enforces that).
#
# This was 0.7, on the reasoning that MediaPipe's own 0.5 hallucinates hands in
# a cluttered background and a phantom hand on Apex does not merely grab a card
# — it can fire a gesture and wake you when nobody moved. That worry is real but
# it was never weighed against the cost on the other side, because there was no
# camera here to weigh it with. A calibration run on real hardware measured the
# cost: a hand detected in 46 of 205 frames, and an OPEN hand detected less
# often (11%) than a pinched one (31%), which is backwards — a splayed hand is
# the easy case. The full reasoning lives beside the setting in config.py.
DEFAULT_MIN_CONFIDENCE = 0.5


def choose_delegate(preference: str, try_create):
    """Build a landmarker on the best delegate available. Returns (obj, used, note).

    `try_create(delegate_name)` does the actual construction and raises if that
    delegate is unusable. Injected so the decision is testable without a GPU —
    which matters, because the machine this was written on has neither a GPU nor
    a camera.

    Verified beforehand that MediaPipe RAISES when a GPU context cannot be
    created rather than quietly falling back:

        RuntimeError: Service "kGpuService", required by node ...

    A silent fallback would be the worse outcome by far: you would believe you
    were on GPU for ever. Hence `used` is returned and always reported.
    """
    pref = (preference or "auto").strip().lower()
    if pref not in ("auto", "gpu", "cpu"):
        pref = "auto"

    if pref == "cpu":
        return try_create("CPU"), "CPU", ""

    try:
        return try_create("GPU"), "GPU", ""
    except Exception as e:
        detail = f"{type(e).__name__}: {str(e)[:120]}"
        # An EXPLICIT request that could not be honoured is a louder event than
        # auto quietly settling: the user asked for something and did not get it,
        # and running on CPU while they think otherwise is the fail-open shape.
        note = (f"GPU was requested but is unavailable, falling back to CPU "
                f"({detail})") if pref == "gpu" else \
               (f"GPU unavailable, using CPU ({detail})")
        return try_create("CPU"), "CPU", note


def build_landmarker(num_hands: int = 2):
    """The real factory. Returns (landmarker, delegate_used, note)."""
    from mediapipe.tasks.python import BaseOptions, vision

    def _create(delegate_name: str):
        delegate = getattr(BaseOptions.Delegate, delegate_name)
        return vision.HandLandmarker.create_from_options(
            vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model_path()),
                                         delegate=delegate),
                running_mode=vision.RunningMode.VIDEO,
                num_hands=num_hands,
                min_hand_detection_confidence=getattr(
                    config, "HANDTRACK_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE),
            )
        )

    return choose_delegate(
        getattr(config, "HANDTRACK_DELEGATE", "auto"), _create)


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


def is_open_palm(lms) -> Optional[bool]:
    """A deliberate open hand — the escape gesture for the glass board.

    A generic technique, not read from any other project: a finger counts as
    extended when its tip sits farther from the wrist than that finger's own
    PIP joint does — the ordinary geometric fact that a straightened finger
    reaches further than a curled one, checked per-finger so a fully splayed
    hand is unambiguous. Requires all four non-thumb fingers extended
    simultaneously; a single extended finger (pointing) or two (a peace sign)
    must not read as "open", or the board's cancel gesture would fire on
    ordinary pointing.

    Distance ratios, not raw distances, keep this working at any distance from
    the camera the same way `pinch_ratio` does above.

    Returns None on unusable landmarks — including a degenerate wrist span,
    which would otherwise divide by zero — so a caller can tell "not open"
    from "couldn't tell" rather than treating them alike.
    """
    try:
        wrist = lms[WRIST]
        pairs = ((INDEX_TIP, INDEX_PIP), (MIDDLE_TIP, MIDDLE_PIP),
                 (RING_TIP, RING_PIP), (PINKY_TIP, PINKY_PIP))
    except (IndexError, TypeError):
        return None
    try:
        span = ((lms[WRIST].x - lms[MIDDLE_MCP].x) ** 2 +
                (lms[WRIST].y - lms[MIDDLE_MCP].y) ** 2) ** 0.5
    except (IndexError, AttributeError, TypeError):
        return None
    if span <= 1e-6:
        return None
    try:
        for tip_idx, pip_idx in pairs:
            tip, pip = lms[tip_idx], lms[pip_idx]
            d_tip = ((wrist.x - tip.x) ** 2 + (wrist.y - tip.y) ** 2) ** 0.5
            d_pip = ((wrist.x - pip.x) ** 2 + (wrist.y - pip.y) ** 2) ** 0.5
            # A small margin (in units of hand span), not a bare ">" — a
            # half-curled finger can have its tip trivially farther than its
            # own PIP by noise alone, which would make "mostly open" register
            # as fully open.
            if (d_tip - d_pip) / span < 0.15:
                return False
    except (IndexError, AttributeError, TypeError):
        return None
    return True


def landmarks_to_cursor(lms, *, mirror: bool = True,
                        threshold: Optional[float] = None):
    """One hand's 21 landmarks -> `(x, y, pinched, open_palm)` for the recognizer.

    The index fingertip is the cursor, because a pointing finger is where a
    person believes they are pointing.

    `mirror` flips x into selfie space. A raw webcam frame is not mirrored, so
    without this a hand moving to the user's right travels *left* in the image
    and `swipe_right` fires for a leftward wave — an inverted axis that would be
    maddening to debug and trivially avoidable here.

    `open_palm` defaults to False on unusable landmarks (`is_open_palm` returned
    None) rather than propagating the ambiguity — a cursor is either present or
    it is not, and "cancel" firing on a shrug of missing data would be worse
    than "cancel" simply not firing that frame.
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
    open_palm = bool(is_open_palm(lms))
    if mirror:
        x = 1.0 - x
    # MediaPipe normalizes to the frame, but a hand at the very edge can report
    # slightly outside it. Clamp so window fractions stay window fractions.
    x = min(1.0, max(0.0, x))
    y = min(1.0, max(0.0, y))
    return (x, y, pinched, open_palm)


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
        self._latest_cursors: list = []
        self._jpeg_error_logged = False  # see latest_jpeg
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

    def latest_jpeg(self, quality: int = 55, max_width: int = 640):
        """The current frame as JPEG bytes, for the board's video background.

        Python holds the camera exclusively, so the browser cannot open it and
        the picture has to travel the other way. Downscaled and middling quality
        on purpose: this is a backdrop behind cards at ~15 fps over localhost,
        not footage anyone will look at closely, and a full-resolution stream
        would spend real CPU on something nobody can see.
        """
        frame = self.latest_frame()
        if frame is None:
            return None
        try:
            import cv2
            h, w = frame.shape[:2]
            if w > max_width:
                frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
            ok, buf = cv2.imencode(".jpg", frame,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                self._log_jpeg_failure("cv2.imencode reported failure "
                                       "(returned ok=False) with no exception")
                return None
            return bytes(buf)
        except Exception as e:
            # A tracker that has hands and pinch working but no visible camera
            # backdrop is exactly what this produces if left silent — cursors
            # come from cv2.VideoCapture + MediaPipe, a path that does not run
            # through here at all, so this can fail on its own, invisibly,
            # while gestures keep working. That combination used to look
            # identical to "no camera plugged in" from the board; it no longer
            # does, because it is now printed at least once.
            self._log_jpeg_failure(f"{type(e).__name__}: {e}")
            return None

    def _log_jpeg_failure(self, detail: str) -> None:
        if self._jpeg_error_logged:
            return
        self._jpeg_error_logged = True
        clash = opencv_conflict()
        hint = (f" Likely cause: {len(clash)} OpenCV packages installed "
               f"({', '.join(clash)}) — {opencv_repair_command()}") if clash else ""
        print(f"[HandTrack] The board's camera backdrop failed to encode: "
              f"{detail}.{hint} Hand tracking itself is unaffected — this "
              f"only means the /board page shows no video, not that gestures "
              f"stopped working.")

    def latest_cursors(self):
        """This frame's hands, for anything that needs them outside the loop."""
        with self._lock:
            return list(self._latest_cursors)

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
        clash = opencv_conflict()
        if clash:
            # Not fatal, so this is a warning and not a refusal — but it is the
            # first thing to suspect if cv2 starts behaving strangely.
            print(f"[HandTrack] WARNING: {len(clash)} OpenCV packages installed "
                  f"({', '.join(clash)}). They share one cv2 directory and "
                  f"overwrite each other. Do NOT just uninstall one — that "
                  f"deletes files the survivor needs and leaves a cv2 with no "
                  f"VideoCapture. Remove them all and install one: {_REPAIR}")
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
            self._landmarker, used, note = build_landmarker(num_hands=2)
            if note:
                print(f"[HandTrack] {note}")
            # Always stated, never inferred. Running on CPU while believing you
            # are on GPU is exactly the kind of quiet wrongness this codebase
            # keeps producing.
            print(f"[HandTrack] Inference on {used}, detection confidence "
                  f"{getattr(config, 'HANDTRACK_MIN_CONFIDENCE', DEFAULT_MIN_CONFIDENCE)}.")
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
                # The tuning surface. The shipped HANDTRACK_PINCH_RATIO came
                # from one hand on one camera; print the ratio actually
                # measured so yours can disagree with it out loud.
                r = pinch_ratio(lms)
                shown = f"{r:.3f}" if r is not None else "n/a"
                print(f"[HandTrack] hand={labels[-1] or '?'} x={cur[0]:.3f} "
                      f"y={cur[1]:.3f} pinch_ratio={shown} pinched={cur[2]}")

        cursors = order_by_handedness(cursors, labels)

        # The board reads the SAME cursor list the recognizer does, rather than
        # tracking hands a second time. Two readings of one camera would drift,
        # and then a card would be somewhere your gesture said you were not.
        with self._lock:
            self._latest_cursors = list(cursors)

        if getattr(config, "BOARD_ENABLED", False):
            try:
                from agent.board import get_board
                get_board().apply_hands(cursors)
            except Exception as e:
                # The board is a view. Losing it must not cost us gestures.
                print(f"[Board] apply_hands failed: {e}")

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
