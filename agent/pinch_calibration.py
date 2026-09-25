"""Calibrate the pinch to YOUR hand, from the board, with the prompts on screen.

The pinch is decided by one number: the thumb-to-index gap as a fraction of
the palm (agent/handtrack.pinch_ratio). Below HANDTRACK_PINCH_RATIO a hand is
pinching; above HANDTRACK_PINCH_RELEASE_RATIO it lets go. The shipped 0.70 /
0.78 came from someone else's hand. On a hand whose relaxed, half-curled pose
reads under 0.70, the board grabs things you never pinched and they follow a
hand that is just resting — which is exactly what the first real use found.

So the board runs this: three poses, a few seconds each, prompted on screen —

    open      hand open, fingers apart
    relaxed   hand relaxed and loose, the way it rests — the pose that misfired
    pinch     thumb and index firmly together

Relaxed counts on the OPEN side: it must never be a pinch. The threshold and
release come from scripts/calibrate_pinch.py's recommend_threshold /
recommend_release (already tested, and allowed to refuse when the poses
overlap — a confident wrong number is worse than none). A result is applied
at once (handtrack reads config every frame) and written to .env so it
survives a restart. A refusal changes nothing and says why.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

import config

READY_SECONDS = 2.0
POSE_SECONDS = 3.0
SAMPLE_HZ = 20.0
POSES = (
    ("open", "Hold your hand OPEN, fingers spread, facing the camera"),
    ("relaxed", "Now let it RELAX — loose and natural, the way it rests (not a pinch)"),
    ("pinch", "Now PINCH — thumb and index firmly together"),
)
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

_lock = threading.Lock()
_state: dict = {"phase": "idle"}
_thread: Optional[threading.Thread] = None
_cancel = threading.Event()


def status() -> dict:
    """What the board shows: the phase, the prompt, seconds left, and the result."""
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.clear()
        _state.update(kw)


def compute(samples: dict) -> dict:
    """The result for recorded ratios {"open": [...], "relaxed": [...], "pinch": [...]}.
    Pure: every judgement is testable without a camera."""
    from scripts.calibrate_pinch import recommend_release, recommend_threshold
    not_pinch = list(samples.get("open", [])) + list(samples.get("relaxed", []))
    value, reason = recommend_threshold(not_pinch, samples.get("pinch", []))
    counts = {k: len(v) for k, v in samples.items()}
    if value is None:
        return {"ok": False, "reason": reason, "counts": counts}
    return {"ok": True, "enter": value, "release": recommend_release(value, not_pinch),
            "reason": reason, "counts": counts}


def apply(result: dict, env_path: Path = ENV_PATH) -> str:
    """Use it now and keep it: config (read by the tracker every frame) and .env."""
    config.HANDTRACK_PINCH_RATIO = result["enter"]
    config.HANDTRACK_PINCH_RELEASE_RATIO = result["release"]
    try:
        from scripts.set_env_key import set_key
        set_key(env_path, "HANDTRACK_PINCH_RATIO", str(result["enter"]))
        set_key(env_path, "HANDTRACK_PINCH_RELEASE_RATIO", str(result["release"]))
        return "saved"
    except Exception as e:
        return f"in use now, but not saved to .env ({type(e).__name__}: {e})"


def _run(read_hands: Callable[[], list], sleep: Callable[[float], None], clock: Callable[[], float],
         env_path: Path) -> None:
    samples: dict = {}
    for i, (pose, prompt) in enumerate(POSES):
        # Get ready: the prompt shows before anything is recorded, so the
        # moment of changing pose never lands in either pile.
        end = clock() + READY_SECONDS
        while clock() < end:
            if _cancel.is_set():
                _set(phase="idle"); return
            _set(phase="ready", pose=pose, step=i + 1, steps=len(POSES), prompt=prompt,
                 left=round(end - clock(), 1))
            sleep(0.1)
        got = []
        end = clock() + POSE_SECONDS
        while clock() < end:
            if _cancel.is_set():
                _set(phase="idle"); return
            got += [h["ratio"] for h in read_hands() if h.get("ratio") is not None]
            _set(phase="recording", pose=pose, step=i + 1, steps=len(POSES), prompt=prompt,
                 left=round(end - clock(), 1), samples=len(got))
            sleep(1.0 / SAMPLE_HZ)
        samples[pose] = got
    result = compute(samples)
    if result["ok"]:
        result["saved"] = apply(result, env_path)
    _set(phase="done", **result)


def start(read_hands: Callable[[], list], *, sleep=time.sleep, clock=time.monotonic,
          env_path: Path = ENV_PATH, background: bool = True) -> dict:
    """Begin a calibration; the board polls status() for the prompts."""
    global _thread
    with _lock:
        if _state.get("phase") in ("ready", "recording"):
            return dict(_state)
    _cancel.clear()
    _set(phase="ready", pose=POSES[0][0], step=1, steps=len(POSES), prompt=POSES[0][1], left=READY_SECONDS)
    args = (read_hands, sleep, clock, env_path)
    if background:
        _thread = threading.Thread(target=_run, args=args, daemon=True, name="PinchCalibration")
        _thread.start()
    else:
        _run(*args)
    return status()


def cancel() -> dict:
    _cancel.set()
    _set(phase="idle")
    return status()


def reset() -> None:
    """For tests."""
    _cancel.set()
    _set(phase="idle")
