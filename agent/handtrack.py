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

import math
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
INDEX_MCP = 5
PINKY_MCP = 17

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

# The 3D measure (pinch_ratio with world landmarks) reads on a different
# scale from the flat one those numbers were calibrated on: a thumb 4 cm
# behind the index finger reads 0.47 in 3D, well under 0.70 — still a
# "pinch". Until the board's calibration has measured YOUR hand in 3D
# (HANDTRACK_PINCH_MEASURE=3d), 3D hands use these instead: about 3 cm
# between the fingertips on an 8.5 cm palm starts a pinch, about 3.8 cm ends
# it. Estimates from ordinary hand proportions, not a measurement of anyone —
# calibrating replaces them. They err on the strict side because a false
# grab drags things around; a stiff pinch only asks for a firmer one.
PINCH_3D_ENTER = 0.35
PINCH_3D_RELEASE = 0.45

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

# How long to wait before trying the camera again after it refuses to open.
#
# There was no wait at all: _open() is called from _tick(), so a machine with no
# camera — or one whose camera is busy in a video call — rebuilt a
# cv2.VideoCapture every 50 ms, forever. Each attempt is a full V4L2 + FFMPEG
# device enumeration, so this burned real CPU at 20 Hz and wrote several lines
# of OpenCV C++ stderr per attempt for as long as Apex ran. The status *message*
# was deduplicated by _say(); the attempts underneath it never were, which is
# why it looked quiet from the Python side while the log filled from below.
#
# Backs off to CAMERA_RETRY_MAX and stays there. The cap is a trade stated
# plainly: plug a camera in and tracking resumes within half a minute rather
# than instantly. resume() and a successful open both reset it, so the paths
# where a person is actually waiting — handing the camera back after a call —
# retry immediately.
CAMERA_RETRY_FIRST = 1.0
CAMERA_RETRY_MAX = 30.0


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
    # Called only after the tracker has opened a camera. An absent camera
    # must be reported even when a model download is slow or unavailable.
    if ensure_model() is None:
        raise RuntimeError("Hand model is unavailable; check the download connection.")

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


def pinch_ratio(lms, world=None) -> Optional[float]:
    """Thumb-to-index distance as a fraction of the hand's own span.

    Scale-invariant by construction, which is what makes one threshold work at
    arm's length and up close. Returns None if the landmarks are unusable —
    including a degenerate span, which would otherwise divide by zero and take
    the tracker thread down with it.

    `world` is MediaPipe's hand_world_landmarks for the same hand: real 3D
    positions in metres. When present the gap and span are measured in 3D.
    Measured flat on the picture, a hand turned side-on to the camera with
    the thumb BEHIND the index finger reads as touching — the first real use
    of the board grabbed exactly like that, "without even fully pinching",
    and no threshold can fix it because in 2D the two poses are the same. In
    3D the hidden thumb is centimetres away. The flat picture also measured
    across and down in different units (image width vs height); metres are
    the same in every direction. Without world landmarks (older callers,
    tests), the 2D measure is used as before.
    """
    three_d = _usable(world)
    pts = world if three_d else lms
    try:
        t, i = pts[THUMB_TIP], pts[INDEX_TIP]
        w, m = pts[WRIST], pts[MIDDLE_MCP]
    except (IndexError, TypeError):
        return None
    try:
        if three_d:
            gap = ((t.x - i.x) ** 2 + (t.y - i.y) ** 2 + (t.z - i.z) ** 2) ** 0.5
            span = ((w.x - m.x) ** 2 + (w.y - m.y) ** 2 + (w.z - m.z) ** 2) ** 0.5
        else:
            gap = ((t.x - i.x) ** 2 + (t.y - i.y) ** 2) ** 0.5
            span = ((w.x - m.x) ** 2 + (w.y - m.y) ** 2) ** 0.5
    except (AttributeError, TypeError):
        return None
    if span <= 1e-6:
        return None
    return gap / span


def _usable(world) -> bool:
    """Whether a world-landmark list has the four points pinch_ratio needs, in 3D."""
    try:
        return world is not None and len(world) > max(THUMB_TIP, INDEX_TIP, WRIST, MIDDLE_MCP) and all(
            hasattr(world[k], "z") for k in (THUMB_TIP, INDEX_TIP, WRIST, MIDDLE_MCP))
    except TypeError:
        return False


# A fist is not a pinch. In a fist the thumb rests on the curled index finger,
# which reads as a closed pinch — and the first real use of the board grabbed
# on a fist. What tells them apart is where the INDEX TIP goes: tucked into
# the palm in a fist, out in front meeting the thumb in a pinch. (A "finger
# curled" test does not work: in an ordinary pinch the index bends too.)
# The index tip closer to the palm's centre than this fraction of the palm
# length means a fist. An estimate from hand proportions — the gesture
# recordings (agent/gesture_recorder.py) are what will set it properly.
FIST_INDEX_TO_PALM = 0.55


def is_fist(lms, world=None) -> bool:
    """Whether the hand is a fist: the index fingertip tucked into the palm.

    Measured in 3D (world landmarks, metres) when available, like pinch_ratio,
    else flat on the picture. The palm's centre is the middle of the wrist and
    the index and pinky base knuckles. False when the landmarks are unusable:
    "couldn't tell" must not veto a pinch.
    """
    pts = world if _usable(world) else lms
    three_d = pts is world
    try:
        need = (WRIST, INDEX_MCP, PINKY_MCP, MIDDLE_MCP, INDEX_TIP)
        p = {k: pts[k] for k in need}
        coords = {k: (v.x, v.y, getattr(v, "z", 0.0) if three_d else 0.0) for k, v in p.items()}
    except (IndexError, TypeError, AttributeError):
        return False
    cx = [sum(coords[k][i] for k in (WRIST, INDEX_MCP, PINKY_MCP)) / 3 for i in range(3)]
    def dist(a, b):
        return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5
    span = dist(coords[WRIST], coords[MIDDLE_MCP])
    if span <= 1e-6:
        return False
    # A palm with no width (the index and pinky knuckles on top of each other)
    # is not a real hand's geometry — a hand seen exactly edge-on, or bad
    # landmarks. Its "centre" means nothing, so it is not called a fist.
    if dist(coords[INDEX_MCP], coords[PINKY_MCP]) < 0.3 * span:
        return False
    return dist(coords[INDEX_TIP], cx) / span < FIST_INDEX_TO_PALM


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


def fingertip_positions(lms, *, mirror: bool = True) -> dict:
    """Live visual feedback only; never substitute these raw points for input."""
    points = {}
    for name, index in (("thumb", THUMB_TIP), ("index", INDEX_TIP),
                        ("middle", MIDDLE_TIP), ("ring", RING_TIP), ("pinky", PINKY_TIP)):
        try:
            x, y = float(lms[index].x), float(lms[index].y)
            if not (math.isfinite(x) and math.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
                continue
            points[name] = [round(1 - x if mirror else x, 4), round(y, 4)]
        except (IndexError, AttributeError, TypeError, ValueError):
            continue
    return points


def hand_joints(lms, *, mirror: bool = True) -> list:
    """All 21 joints in board space, for drawing the hand (the study's
    holographic hand). Visual only, like fingertip_positions: input decisions
    never read these. [] unless every joint is a finite point in the frame
    (MediaPipe can report slightly outside it; those are clamped)."""
    out = []
    try:
        for p in lms[:21]:
            x, y = float(p.x), float(p.y)
            if not (math.isfinite(x) and math.isfinite(y)):
                return []
            x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
            out.append([round(1 - x if mirror else x, 4), round(y, 4)])
    except (TypeError, AttributeError, ValueError):
        return []
    return out if len(out) == 21 else []


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


# A hand MediaPipe misses for a frame or two (motion blur on a fast drag, a
# finger crossing the palm) is still the same hand. Its identity, its pinch
# latch and anything it holds survive this long unseen; only after it do they
# count as gone. One number, owned by the board, so the hold and the identity
# can never expire at different moments.
from agent.board import HAND_LOSS_GRACE_SECONDS  # noqa: E402
# How far a hand may move between sightings and still be the same hand:
# a base radius plus a speed allowance for the time it was unseen.
MATCH_RADIUS = 0.15
MATCH_SPEED = 2.0          # frame-widths per second


# Release sits this far above entry when HANDTRACK_PINCH_RELEASE_RATIO is unset.
PINCH_RELEASE_MARGIN = 0.08


def pinch_release_ratio(enter: float) -> float:
    """The ratio a pinch must rise above to end. Never below entry — a release
    below entry would make no sense, so it degrades to no hysteresis."""
    configured = getattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", None)
    release = float(configured) if configured is not None else enter + PINCH_RELEASE_MARGIN
    return max(release, float(enter))


class OneEuro:
    """The One-Euro filter (Casiez, Roussel & Vogel, CHI 2012), for one value.

    A low-pass filter whose cutoff rises with speed: a hand held still is
    smoothed hard, which takes out MediaPipe's frame-to-frame shiver, and a
    hand moving fast is barely smoothed, so a quick move is not left behind.
    A fixed smoothing has to choose between shivering and lagging; this is
    the standard answer to that for pointers. A generic published technique.
    """

    def __init__(self, min_cutoff: float, beta: float, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self.t is None or t <= self.t:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = t - self.t
        dx = (x - self.x) / dt
        self.dx = self.dx + self._alpha(self.d_cutoff, dt) * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x = self.x + self._alpha(cutoff, dt) * (x - self.x)
        self.t = t
        return self.x


# Tuning for hand positions in board fractions (0..1 of the frame), chosen
# from a sweep at 30 frames a second: with these, a still hand's shiver drops
# about 3x, and a hand moving two screen-widths a second trails by ~2% of the
# screen. Lower cutoffs steady more and lag more; there is no setting that
# does both perfectly, because shiver and slow motion look alike to any
# filter. Estimates on synthetic jitter until the gesture recordings measure
# a real hand. (The board's glide adds ~45 ms of easing on top.)
SMOOTH_MIN_CUTOFF = 0.5
SMOOTH_BETA = 5.0


class HandIdentities:
    """Stable ids for hands across frames, by where they are.

    Everything downstream — the pinch latch, the board's holds — needs "the
    same hand as last frame", and neither thing MediaPipe offers is that.
    Detection order renumbers between frames. The Left/Right label flips on
    a single frame and duplicates ("Right", "Right") happen. Keying on either
    moved a held card to the OTHER hand the moment a second hand came into
    view, which threw it away, dropped it, or snapped it back.

    Position is the evidence that holds up: a hand is where it just was, give
    or take how fast a hand can move. Each new sighting is paired with the
    closest live track (closest pairs first, so two hands near each other
    cannot steal each other's identity); anything unpaired is a new hand with
    a new id. Ids are never reused, so a hand that left and came back is a
    new hand — it does not resume a pinch from before it left.
    """

    def __init__(self):
        self._tracks: dict[int, tuple[float, float, float]] = {}
        self._next = 0

    def assign(self, points: list, now: float) -> list:
        self._tracks = {k: v for k, v in self._tracks.items()
                        if now - v[2] <= HAND_LOSS_GRACE_SECONDS}
        pairs = []
        for i, (x, y) in enumerate(points):
            for k, (tx, ty, ts) in self._tracks.items():
                d = math.hypot(x - tx, y - ty)
                if d <= MATCH_RADIUS + MATCH_SPEED * max(0.0, now - ts):
                    pairs.append((d, i, k))
        pairs.sort()
        ids: list = [None] * len(points)
        used = set()
        for _d, i, k in pairs:
            if ids[i] is None and k not in used:
                ids[i] = k
                used.add(k)
        for i, (x, y) in enumerate(points):
            if ids[i] is None:
                ids[i] = self._next
                self._next += 1
            self._tracks[ids[i]] = (x, y, now)
        return ids

    def alive(self) -> set:
        """Ids seen within the grace period, including ones missing right now."""
        return set(self._tracks)


class PinchLatch:
    """Per-hand pinch with hysteresis: starts below `enter`, ends above `release`.

    One threshold decided afresh every frame is what the board had, and on real
    hardware it produced a 70% grab rate where every miss was "picked up, then
    dropped". A pinch that sits near 0.70 reads 0.69, 0.71, 0.69 on successive
    frames; each 0.71 was an "open" frame, and one open frame releases a held
    card. The board's own docstring cites a gesture contract of "confidence,
    dwell time, hysteresis, and cooldown" — dwell was built, hysteresis was not.

    Keyed by HandIdentities id — not detection order, which renumbers, and not
    MediaPipe's Left/Right, which flips for a frame and resets the latch.

    A ratio of None — landmarks unusable for a frame — KEEPS the previous state.
    That is the same rule `_tick` applies to a dropped camera frame: a missing
    observation is not an observation that the hand opened, and treating it as
    one is a second way to drop a card mid-move.
    """

    def __init__(self):
        self._on: dict = {}

    def update(self, key, ratio, enter: float, release: float) -> bool:
        release = max(float(release), float(enter))   # release <= enter: no hysteresis
        was = self._on.get(key, False)
        if ratio is None:
            now = was
        elif was:
            now = ratio < release
        else:
            now = ratio < enter
        self._on[key] = now
        return now

    def keep_only(self, keys) -> None:
        """Forget hands that are gone — pass HandIdentities.alive(), so a hand
        missed for a frame keeps its pinch and a hand that left does not."""
        keys = set(keys)
        for k in list(self._on):
            if k not in keys:
                del self._on[k]


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
        # Per-hand detail for the board's live readout. Kept BESIDE the cursor
        # list rather than widening it: `latest_cursors` feeds the gesture
        # recognizer and the board, and both unpack 4-tuples.
        self._latest_hands: list = []
        self._study_sequence = 0
        self._study_sample_at = 0.0
        self._jpeg_error_logged = False  # see latest_jpeg
        self._cap = None
        self._landmarker = None
        self._frame_no = 0
        self._reported = ""
        self._retry_at = 0.0            # earliest next camera-open attempt
        self._retry_delay = CAMERA_RETRY_FIRST

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
            self._reset_retry()
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
            # The hands are mirrored into selfie space (HANDTRACK_MIRROR), so
            # the picture behind them must be too. It went out raw: move your
            # hand left and the ring went left while your image went right —
            # "everything is in reverse". Only the picture sent to the board
            # is flipped; latest_frame() (the camera tool) stays as captured.
            if getattr(config, "HANDTRACK_MIRROR", True):
                frame = cv2.flip(frame, 1)
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

    def latest_hands(self) -> list:
        """The same hands with the numbers that decided the pinch.

        For the board's readout: a pinch that does not grab is otherwise
        indistinguishable from a camera that sees nothing, and both look like
        "it is broken".
        """
        with self._lock:
            return [dict(d) for d in self._latest_hands]

    def study_sample(self) -> dict:
        """Timestamp detection results, not camera frames, to reject stale grabs."""
        with self._lock:
            stamp = getattr(self, '_study_sample_at', 0.0)
            age = max(0, time.monotonic() - stamp) if stamp else None
            return {'sequence': getattr(self, '_study_sequence', 0),
                    'age_ms': round(age * 1000) if age is not None else None,
                    'hands': [dict(d) for d in self._latest_hands] if age is not None and age < .4 else []}

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
        print(f"[HandTrack] Watching camera {self.device_index} at "
              f"{1 / self.interval:.0f} Hz.")
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    self._tick(time.time())
                except Exception as e:
                    # A raise here would end hand tracking for the rest of the
                    # session with nothing but a dead thread to show for it.
                    print(f"[HandTrack] tick error: {e}")
                # Capture/inference already consumed part (or all) of the
                # frame budget. Do not add a whole interval after that work.
                self._stop.wait(timeout=max(0.0, self.interval - (time.monotonic() - started)))
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

    def _open(self, now: Optional[float] = None) -> bool:
        import cv2
        now = time.time() if now is None else now
        if self._cap is None:
            if now < self._retry_at:
                return False
            self._cap = cv2.VideoCapture(self.device_index)
            if not self._cap.isOpened():
                self._cap = None
                self._retry_at = now + self._retry_delay
                self._say("camera_busy",
                          f"[HandTrack] Camera {self.device_index} would not "
                          f"open — something else may be using it. Retrying "
                          f"every {self._retry_delay:.0f}s.")
                self._retry_delay = min(self._retry_delay * 2, CAMERA_RETRY_MAX)
                return False
            self._reset_retry()
            self._say("camera_open", "[HandTrack] Camera open.")
        if self._landmarker is None:
            import mediapipe as mp
            model_started = time.monotonic()
            try:
                self._landmarker, used, note = build_landmarker(num_hands=2)
            except Exception as exc:
                self._teardown()
                self._retry_at = now + (time.monotonic() - model_started) + CAMERA_RETRY_MAX
                self._say("model_unavailable", f"[HandTrack] Hand model unavailable: {exc}. Retrying in {CAMERA_RETRY_MAX:.0f}s.")
                return False
            if note:
                print(f"[HandTrack] {note}")
            # Always stated, never inferred. Running on CPU while believing you
            # are on GPU is exactly the kind of quiet wrongness this codebase
            # keeps producing.
            print(f"[HandTrack] Inference on {used}, detection confidence "
                  f"{getattr(config, 'HANDTRACK_MIN_CONFIDENCE', DEFAULT_MIN_CONFIDENCE)}.")
            self._mp = mp
        return True

    def _reset_retry(self) -> None:
        """Back to trying immediately. Called when the camera opens, and when a
        person hands it back — waiting out a 30-second backoff after explicitly
        saying "resume" would read as the resume having failed."""
        self._retry_at = 0.0
        self._retry_delay = CAMERA_RETRY_FIRST

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

        if not self._open(now):
            self.recognizer.feed_cursors(None, now)
            return

        captured_at = time.monotonic()
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
        # Real monotonic capture time, not an assumed fixed detector cadence.
        # Keep timestamps strictly increasing even within one millisecond.
        self._frame_no += 1
        self._detector_timestamp_ms = max(int(captured_at * 1000), getattr(self, '_detector_timestamp_ms', 0) + 1)
        result = self._landmarker.detect_for_video(
            image, self._detector_timestamp_ms)

        try:
            from agent import gesture_recorder
            gesture_recorder.observe(result, now)      # no-op unless recording
        except Exception as e:
            print(f"[HandTrack] gesture recording failed: {e}")
        cursors, details = self._read_hands(result, now)

        # The board reads the SAME cursor list the recognizer does, rather than
        # tracking hands a second time. Two readings of one camera would drift,
        # and then a card would be somewhere your gesture said you were not.
        with self._lock:
            self._latest_cursors = list(cursors)
            self._latest_hands = list(details)
            self._study_sequence = getattr(self, '_study_sequence', 0) + 1
            # Include capture and inference time in freshness. A slow result
            # must not pretend to be a brand-new observation when published.
            self._study_sample_at = captured_at

        if getattr(config, "BOARD_ENABLED", False):
            try:
                from agent.board import get_board
                # The same `now`: the hold's grace period and the identity's
                # must expire on one clock, not two that drift.
                get_board().apply_hands(cursors, now=now)
            except Exception as e:
                # The board is a view. Losing it must not cost us gestures.
                print(f"[Board] apply_hands failed: {e}")

        for g in self.recognizer.feed_cursors(cursors, now):
            self._dispatch(g)

    def _read_hands(self, result, now: Optional[float] = None):
        """One frame's MediaPipe result -> (cursors, details), pinch latched.

        Pulled out of `_tick` so it can be driven with a recorded sequence of
        frames. The bug it fixes only exists ACROSS frames, and a loop that
        cannot be fed frames without a camera could not have a test for it.

        Each cursor is `(x, y, pinched, open_palm, hand_id)`. The id is what
        the board keys holds on and the latch keys pinches on; see
        HandIdentities for why it is not the label or the list position.
        """
        now = now if now is not None else time.time()
        mirror = getattr(config, "HANDTRACK_MIRROR", True)
        enter = float(getattr(config, "HANDTRACK_PINCH_RATIO", DEFAULT_PINCH_RATIO))
        release = pinch_release_ratio(enter)
        if not hasattr(self, "_latch"):
            self._latch = PinchLatch()
        if not hasattr(self, "_ids"):
            self._ids = HandIdentities()

        raw = list(result.hand_landmarks or [])
        worlds = list(getattr(result, "hand_world_landmarks", None) or [])
        found = []
        for idx, lms in enumerate(raw):
            cur = landmarks_to_cursor(lms, mirror=mirror)
            if cur is not None:
                found.append((idx, lms, cur))
        ids = self._ids.assign([(cur[0], cur[1]) for _i, _l, cur in found], now)

        if not hasattr(self, "_smooth"):
            self._smooth = {}
        rows = []
        for (idx, lms, cur), hid in zip(found, ids):
            # Smoothed per hand (by identity, so a second hand arriving never
            # inherits the first one's filter state).
            fx, fy = self._smooth.setdefault(hid, (OneEuro(SMOOTH_MIN_CUTOFF, SMOOTH_BETA),
                                                   OneEuro(SMOOTH_MIN_CUTOFF, SMOOTH_BETA)))
            cur = (fx(cur[0], now), fy(cur[1], now)) + tuple(cur[2:])
            world = worlds[idx] if idx < len(worlds) else None
            r = pinch_ratio(lms, world)
            e, rl = enter, release
            if _usable(world) and getattr(config, "HANDTRACK_PINCH_MEASURE", "") != "3d":
                e, rl = PINCH_3D_ENTER, PINCH_3D_RELEASE
            fist = is_fist(lms, world)
            # A fist lets go of anything held and never starts a pinch: fed
            # to the latch as a wide-open hand, not as "unknown" (which would
            # keep whatever state it had).
            pinched = self._latch.update(hid, (rl + 1.0) if fist else r, e, rl)
            label = _handedness_label(result, idx) or "?"
            cur = (cur[0], cur[1], pinched, cur[3], hid)
            # Measured once and kept, not measured once and printed. The ratio
            # is the number that decides whether a pinch happens, and it used
            # to exist only inside a HANDTRACK_DEBUG print — a scrolling log,
            # which 7fc8f34 already concluded is the wrong place to read a
            # threshold off. The board shows it live instead.
            detail = {
                "id": hid, "label": label,
                "x": round(cur[0], 4), "y": round(cur[1], 4),
                "ratio": round(r, 4) if r is not None else None,
                "measure": "3d" if _usable(world) else "2d",
                "threshold": round(e, 4),
                "release": round(rl, 4),
                "pinched": bool(pinched),
                "open_palm": bool(cur[3]),
                "fist": bool(fist),
                "fingertips": fingertip_positions(lms, mirror=mirror),
                "joints": hand_joints(lms, mirror=mirror),
            }
            rows.append((hid, cur, detail))
            if getattr(config, "HANDTRACK_DEBUG", False):
                shown = f"{r:.3f}" if r is not None else "n/a"
                print(f"[HandTrack] hand={hid}/{label} x={cur[0]:.3f} "
                      f"y={cur[1]:.3f} pinch_ratio={shown} pinched={pinched}")
        # Forget only hands that are GONE. Forgetting every hand not seen this
        # frame reset a pinch on one missed detection, which is one of the
        # ways a held card was dropped mid-drag.
        self._latch.keep_only(self._ids.alive())
        alive = set(self._ids.alive())
        for gone in [h for h in self._smooth if h not in alive]:
            del self._smooth[gone]

        # Oldest hand first, details reordered WITH the cursors, so the
        # readout never pairs one hand's ratio with the other's grab state.
        rows.sort(key=lambda row: row[0])
        return [c for _h, c, _d in rows], [d for _h, _c, d in rows]

    def _board_voice(self, action: str) -> None:
        try:
            sent = board_voice_event(action)
            if sent:
                self.log.add("gesture", f"board voice: {sent}")
        except Exception as e:
            print(f"[HandTrack] board voice event failed: {e}")

    def _board_gesture(self, gesture: str, action: str) -> None:
        if not getattr(config, "BOARD_ENABLED", False):
            return
        try:
            what = run_board_action(action)
        except Exception as e:
            what = f"failed: {e}"
        if what.startswith("ignored"):
            self.recognizer.refund(gesture)
        self.log.add("gesture", f"{gesture} -> {action}: {what}")

    def _board_blocks(self, gesture: str) -> str:
        """Why the board vetoes this gesture right now, or "".

        The recognizer knows nothing about cards. Moving a held card IS a
        swipe to it, and holding one still IS a pinch_hold. Only board:*
        actions used to be checked, so letting go of a card dragged downward
        fired swipe_down -> stop, and holding a card still fired
        pinch_hold -> listen every three seconds.
        """
        from agent import study_input
        if study_input.active():
            return "study owns hand controls"
        if not getattr(config, "BOARD_ENABLED", False):
            return ""
        try:
            from agent.board import get_board
            board = get_board()
            if not board.hands_enabled:
                return "hand controls are paused"
            if gesture.startswith("swipe_"):
                ok, why = board.swipes_allowed()
                return "" if ok else why
            if gesture == "pinch_hold":
                ok, why = board.hands_idle()
                return "" if ok else why
        except Exception as e:
            print(f"[HandTrack] board check failed: {e}")
        return ""

    def _dispatch(self, gesture: str) -> None:
        from agent import gestures as _g
        # Always logged, whatever the allowlist says: recognition and action are
        # separate gates, so "I waved and nothing happened" stays diagnosable.
        self.log.add("gesture", _g.describe(gesture))
        action = _g.gesture_action(gesture)
        if action:
            blocked = self._board_blocks(gesture)
            if blocked:
                # Refunded: a gesture that did nothing must not spend the
                # cooldown the next deliberate one needs.
                self.recognizer.refund(gesture)
                self.log.add("gesture", f"{gesture} -> {action}: ignored ({blocked})")
                return
        if action in ("stop", "listen", "wake"):
            # The board's Voice (Celine in the partner panel) hears these too:
            # stop -> she stops speaking; listen/wake -> she starts listening.
            # The main voice loop's handler below still runs as before.
            self._board_voice(action)
        if action and action.startswith("board:"):
            # Board gestures are handled HERE, not through on_gesture. That
            # hook is only ever set by app/resident.py, so in `main.py --text`
            # — the way Apex is normally run — every mapped gesture was
            # recognised, logged and then did nothing at all.
            self._board_gesture(gesture, action)
            return
        if action and self.on_gesture:
            try:
                self.on_gesture(gesture, action)
            except Exception as e:
                print(f"[HandTrack] gesture handler error: {e}")


def board_voice_event(action: str, board=None) -> str:
    """Tell an open /board page about a voice gesture: "hush" for stop,
    "listen" for listen or wake. Returns what was sent, for the log."""
    if not getattr(config, "BOARD_ENABLED", False):
        return ""
    if board is None:
        from agent.board import get_board
        board = get_board()
    kind = "hush" if action == "stop" else "listen"
    board.emit(kind)
    return kind


# What each board action does. Named rather than inlined so the allowlist in
# HANDTRACK_GESTURE_ACTIONS can only point at something that exists — an
# unknown "board:x" is reported, not silently ignored.
BOARD_ACTIONS = ("board:summon", "board:next", "board:prev")


def run_board_action(action: str, board=None) -> str:
    """Carry out one board gesture. Returns what happened, for the log.

    A module function rather than a method so it can be exercised without a
    camera, a thread or a tracker.
    """
    if board is None:
        from agent.board import get_board
        board = get_board()
    allowed, why = board.swipes_allowed()
    if not allowed:
        return f"ignored ({why})"
    if action == "board:summon":
        board.emit("summon")
        return "summoned Apex"
    if action in ("board:next", "board:prev"):
        card = board.select_step(1 if action == "board:next" else -1)
        if card is None:
            return "nothing on the board to select"
        board.emit("selected", id=card["id"], title=card["title"])
        return f"selected {card['title']}"
    return f"unknown board action {action!r} — check HANDTRACK_GESTURE_ACTIONS"


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
