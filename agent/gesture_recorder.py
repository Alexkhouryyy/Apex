"""Record YOUR hand doing each gesture, so the board can be tuned on it.

Every threshold on the board — where a pinch starts, what a fist is, how fast
a swipe is, how short a tap is — was set by estimate, and on the first real
hand most of them were wrong. Guessing again will not fix that. This records
what the tracker actually sees while you do each gesture on cue, and
tools/replay_gestures.py runs a recording back through the real tracker,
board and gesture recogniser and reports, per gesture, what fired.

What is recorded: MediaPipe's 21 hand joints per hand per frame — positions
in the picture and in 3D — the handedness, the time, and which prompt was on
screen. NOT the camera picture: no image or video is kept. The file is saved
as recordings/gestures-<time>.json.gz in the Apex folder (about half a
megabyte), for the user to send.

The prompts run on the board (like the pinch calibration): a get-ready
countdown, then the recording window. Frames during a get-ready countdown are
not labelled with the pose, because changing pose is not the pose.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

READY_SECONDS = 1.5
FORMAT = 1

# (take, prompt, seconds). One take per line; repeats are separate takes so
# each tap and each swipe is its own labelled window.
SCRIPT = [
    ("open", "Hold your hand OPEN, fingers spread, facing the camera", 3),
    ("relaxed", "Let your hand RELAX — loose, the way it rests", 3),
    ("fist", "Make a FIST", 3),
    ("side_on", "Turn your hand SIDE-ON, thumb behind your index finger — not touching", 3),
    ("pinch_hold", "PINCH and hold it", 3),
    ("tap_1", "Quick TAP: pinch and let go, once", 2),
    ("tap_2", "Quick TAP again", 2),
    ("tap_3", "One more quick TAP", 2),
    ("grab_move", "PINCH, move your hand across the screen, then let go", 4),
    ("two_hands", "BOTH hands: pinch, pull apart, then bring together", 5),
    ("open_palm", "OPEN PALM facing the camera, held still", 3),
    ("swipe_up_1", "SWIPE UP with an open hand", 2),
    ("swipe_up_2", "SWIPE UP again", 2),
    ("swipe_down_1", "SWIPE DOWN with an open hand", 2),
    ("swipe_down_2", "SWIPE DOWN again", 2),
    ("swipe_left_1", "SWIPE LEFT with an open hand", 2),
    ("swipe_right_1", "SWIPE RIGHT with an open hand", 2),
    ("idle", "Move your hands around naturally — do NOT pinch", 5),
]
OUT_DIR = Path(__file__).resolve().parents[1] / "recordings"

_lock = threading.Lock()
_state: dict = {"phase": "idle"}
_frames: list = []
_cancel = threading.Event()
_active_label: Optional[str] = None
_t0 = 0.0


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.clear()
        _state.update(kw)


def _points(lms, digits: int) -> list:
    try:
        return [[round(float(p.x), digits), round(float(p.y), digits), round(float(getattr(p, "z", 0.0)), digits)]
                for p in lms]
    except (TypeError, AttributeError, ValueError):
        return []


def observe(result, now: float) -> None:
    """Called by the tracker with every frame's raw MediaPipe result. Cheap
    when nothing is recording; while a prompt's window is open, keeps the
    joints (never the picture)."""
    label = _active_label
    if label is None:
        return
    worlds = list(getattr(result, "hand_world_landmarks", None) or [])
    hands = []
    for i, lms in enumerate(getattr(result, "hand_landmarks", None) or []):
        try:
            side = result.handedness[i][0].category_name
        except Exception:
            side = "?"
        hands.append({"lm": _points(lms, 4),
                      "world": _points(worlds[i], 5) if i < len(worlds) else [],
                      "side": side})
    with _lock:
        _frames.append({"t": round(now - _t0, 4), "take": label, "hands": hands})


def _run(sleep, clock, out_dir: Path, script) -> None:
    global _active_label, _t0
    _t0 = time.time()
    with _lock:
        _frames.clear()
    for i, (take, prompt, seconds) in enumerate(script):
        for phase, length in (("ready", READY_SECONDS), ("recording", seconds)):
            _active_label = take if phase == "recording" else None
            end = clock() + length
            while clock() < end:
                if _cancel.is_set():
                    _active_label = None
                    _set(phase="idle")
                    return
                with _lock:
                    n = len(_frames)
                _set(phase=phase, take=take, step=i + 1, steps=len(script), prompt=prompt,
                     left=round(end - clock(), 1), frames=n)
                sleep(0.05)
    _active_label = None
    with _lock:
        frames = list(_frames)
    takes = {t for t, _p, _s in script}
    seen = {f["take"] for f in frames if f["hands"]}
    missing = sorted(takes - seen)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / time.strftime("gestures-%Y%m%d-%H%M%S.json.gz")
    doc = {"format": FORMAT, "recorded": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "script": [{"take": t, "prompt": p, "seconds": s} for t, p, s in script],
           "frames": frames}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    _set(phase="done", path=str(path), frames=len(frames), size_kb=round(path.stat().st_size / 1024),
         missing=missing)


def start(*, sleep=time.sleep, clock=time.monotonic, out_dir: Path = OUT_DIR,
          script=None, background: bool = True) -> dict:
    with _lock:
        if _state.get("phase") in ("ready", "recording"):
            return dict(_state)
    _cancel.clear()
    script = script or SCRIPT
    _set(phase="ready", take=script[0][0], step=1, steps=len(script), prompt=script[0][1],
         left=READY_SECONDS, frames=0)
    args = (sleep, clock, out_dir, script)
    if background:
        threading.Thread(target=_run, args=args, daemon=True, name="GestureRecorder").start()
    else:
        _run(*args)
    return status()


def cancel() -> dict:
    global _active_label
    _cancel.set()
    _active_label = None
    _set(phase="idle")
    return status()


def reset() -> None:
    """For tests."""
    cancel()
    with _lock:
        _frames.clear()


def load(path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
