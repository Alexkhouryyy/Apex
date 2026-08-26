"""Measure HANDTRACK_PINCH_RATIO against a real hand instead of guessing it.

The shipped default (0.45) was chosen on a machine with no camera. It is the one
number that decides whether pinch works at all, and if it is wrong **pinch simply
never fires and nothing says why** — the gesture is silently absent, which is the
failure shape this project keeps producing.

The alternative on offer was HANDTRACK_DEBUG, which prints a line per hand per
frame at 20 Hz and asks a person to read values out of a scrolling log. That is a
bad instrument: it samples whichever frames the eye happens to catch, it cannot
show the spread, and a number transcribed by hand is exactly how a threshold ends
up subtly wrong in a way that surfaces weeks later as "pinch is unreliable".

    python scripts/calibrate_pinch.py

Hold your hand open when asked, then pinch. It reports both distributions and,
when they are cleanly separated, a threshold — and offers to write it.

## Why this is allowed to fail

`recommend_threshold` returns None when the two clouds overlap. That is the
point. A calibrator that always produces a number is worse than none at all,
because it converts "I could not tell" into a confident setting that misfires
for ever while looking measured.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import handtrack  # noqa: E402

# Below this many usable readings the hand was not really detected — bad light,
# too far from the lens, out of frame. That is not a threshold problem and must
# not be answered with a threshold.
MIN_SAMPLES = 20

# A gap narrower than this separates the clouds but leaves no room for a hand
# held differently tomorrow. Still returns a value, with a warning.
COMFORTABLE_GAP = 0.15

# Below this the clouds are technically ordered but the margin is measurement
# noise — a couple of hundredths either side. Found by running the calibrator
# against a "hand not opened properly" case: it happily returned 0.55 for a gap
# of 0.02 and called it fragile. Fragile understates it. A threshold fitted to
# noise is a confident wrong answer, which is the one thing this must not
# produce, so this is a refusal rather than a warning.
MIN_USABLE_GAP = 0.05

# Where in the gap the boundary sits. Biased toward the open-hand side so a lazy
# pinch — thumb and finger close but not touching — still registers, since the
# cost of missing a pinch is a gesture that appears broken.
GAP_BIAS = 0.6


def percentile(values: list, pct: float) -> float:
    """Simple nearest-rank percentile. Written out rather than pulled from a
    library so it behaves predictably on the small samples this collects."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def clean(samples) -> list:
    """Drop anything that is not a usable number.

    pinch_ratio returns None for landmarks it cannot use, and a NaN would poison
    every comparison downstream while silently passing an `isinstance` check.
    """
    out = []
    for s in samples or []:
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            continue
        if s != s or s in (float("inf"), float("-inf")):   # NaN / inf
            continue
        out.append(float(s))
    return out


def recommend_threshold(open_samples, pinch_samples) -> tuple:
    """(value | None, reason). Pure, so the judgement is testable without a hand.

    Separation is judged on the tails, not the medians: what matters is whether
    the WORST pinch still reads lower than the LOOSEST open hand. Two
    distributions can have far-apart medians and still overlap badly, and a
    threshold placed between the medians would then misfire on both sides.
    """
    o, p = clean(open_samples), clean(pinch_samples)

    if len(o) < MIN_SAMPLES or len(p) < MIN_SAMPLES:
        return None, (
            f"Not enough readings ({len(o)} open, {len(p)} pinched; need "
            f"{MIN_SAMPLES} of each). Your hand was not detected for most of "
            f"the window — try better light, or move closer to the camera. "
            f"This is not a threshold problem, so guessing one would not help."
        )

    pinch_hi = percentile(p, 95)     # the loosest pinch that still counts
    open_lo = percentile(o, 5)       # the tightest open hand

    if pinch_hi >= open_lo:
        return None, (
            f"These cannot be separated: the loosest pinches reach "
            f"{pinch_hi:.2f} while the tightest open readings go down to "
            f"{open_lo:.2f}, so they overlap by {pinch_hi - open_lo:.2f}. Any "
            f"threshold would misfire in both directions. Usually this means "
            f"the open hand was not open enough, or the hand was angled so the "
            f"thumb was hidden — try again facing the camera squarely."
        )

    gap = open_lo - pinch_hi
    if gap < MIN_USABLE_GAP:
        return None, (
            f"The readings are ordered but only {gap:.3f} apart (pinched up to "
            f"{pinch_hi:.2f}, open from {open_lo:.2f}) — that is measurement "
            f"noise, not separation, and a threshold fitted to it would be a "
            f"confident wrong answer. Open your hand wider, spreading the thumb "
            f"well away from the index finger, and run this again."
        )

    value = round(pinch_hi + gap * GAP_BIAS, 2)
    if gap < COMFORTABLE_GAP:
        return value, (
            f"Separated, but only by {gap:.2f}. {value} will work today and is "
            f"likely to be fragile — if pinch starts misfiring, re-run this "
            f"rather than nudging the number by hand."
        )
    return value, (
        f"Cleanly separated by {gap:.2f} (pinched up to {pinch_hi:.2f}, open "
        f"from {open_lo:.2f}). {value} sits in the gap, biased toward the open "
        f"side so a lazy pinch still registers."
    )


def describe(label: str, samples) -> str:
    s = clean(samples)
    if not s:
        return f"  {label}: no readings"
    return (f"  {label}: {len(s)} readings, "
            f"median {percentile(s, 50):.2f}, "
            f"range {min(s):.2f}–{max(s):.2f}")


# ── The interactive half, which needs a camera ───────────────────────────────

def collect(landmarker, cap, mp, seconds: float, frame_no: int,
            preview: bool = False) -> tuple:
    """Sample pinch_ratio for `seconds`. Returns (samples, next_frame_no, stats).

    `stats` counts frames the camera delivered alongside hands MediaPipe found,
    because "0 readings" has two completely different causes — a camera handing
    over nothing, and a camera working fine with no hand in front of it — and
    the first version of this reported them identically. One is fixed by
    reinstalling a driver, the other by turning a light on.
    """
    import cv2
    samples, deadline = [], time.time() + seconds
    frames_ok = frames_failed = hands_seen = 0
    while time.time() < deadline:
        ok, frame = cap.read()
        if not ok or frame is None:
            frames_failed += 1
            continue
        frames_ok += 1
        frame_no += 1
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = landmarker.detect_for_video(image, frame_no * 50)
        hands = result.hand_landmarks or []
        hands_seen += len(hands)
        for lms in hands:
            r = handtrack.pinch_ratio(lms)
            if r is not None:
                samples.append(r)
        if preview:
            _show(cv2, frame, hands)
        left = max(0.0, deadline - time.time())
        print(f"\r    {left:4.1f}s   frames {frames_ok}   hands {hands_seen}   "
              f"readings {len(samples)}   ", end="", flush=True)
    print()
    return samples, frame_no, {"frames": frames_ok, "dropped": frames_failed,
                               "hands": hands_seen}


def _show(cv2, frame, hands) -> None:
    """Draw what the camera sees, with any detected landmarks on top.

    Seeing the frame answers in one second what the numbers take a support
    conversation to establish: whether the camera works at all, and whether the
    hand is in shot.
    """
    try:
        h, w = frame.shape[:2]
        for lms in hands:
            for lm in lms:
                cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 3,
                           (0, 255, 0), -1)
        cv2.putText(frame, f"hands: {len(hands)}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("calibrate_pinch - press any key to abort", frame)
        cv2.waitKey(1)
    except Exception:
        # Preview is a convenience. A build of OpenCV without GUI support must
        # not take the measurement down with it.
        pass


def explain_no_readings(stats_open: dict, stats_pinch: dict) -> str:
    """Say WHICH failure produced zero readings.

    This exists because the first live run reported "hand was not detected —
    try better light" when the actual cause was unknown and could equally have
    been a camera delivering nothing. Advice for the wrong failure is worse
    than no advice: it sends you to fix something that was never broken.
    """
    def total(key: str) -> int:
        # `.get(key, 0)` is not enough: a key present with a None value returns
        # None, and None + None raises. This runs on the failure path, where a
        # traceback would replace the diagnosis with nothing.
        out = 0
        for stats in (stats_open, stats_pinch):
            try:
                out += int((stats or {}).get(key) or 0)
            except (TypeError, ValueError, AttributeError):
                continue
        return out

    frames, dropped, hands = total("frames"), total("dropped"), total("hands")

    if frames == 0:
        return (f"The camera opened but delivered no frames at all ({dropped} "
                f"failed reads). That is a capture problem, not a hand problem. "
                f"Most likely: two OpenCV packages fighting over one cv2 install "
                f"(repair: {handtrack.opencv_repair_command()}), another app holding the "
                f"camera, or a privacy shutter. Re-run with --preview to see "
                f"whether any picture arrives.")
    if hands == 0:
        return (f"The camera works — {frames} frames arrived — but MediaPipe "
                f"found no hand in any of them. That is light, distance or "
                f"framing. Get your whole hand in shot, palm to the camera, "
                f"well lit, roughly an arm's length away. --preview shows you "
                f"exactly what it sees.")
    return (f"{frames} frames, {hands} hand detections, but no usable pinch "
            f"ratio — the landmarks came back degenerate. Face the camera "
            f"squarely rather than edge-on.")


def countdown(message: str, seconds: int = 3) -> None:
    print(f"\n{message}")
    for i in range(seconds, 0, -1):
        print(f"  starting in {i}…", end="\r", flush=True)
        time.sleep(1)
    print("  go!            ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=4.0,
                    help="how long to sample each pose (default 4)")
    ap.add_argument("--device", type=int, default=None,
                    help="camera index (default: CAMERA_DEVICE_INDEX)")
    ap.add_argument("--write", action="store_true",
                    help="write the result to .env without asking")
    ap.add_argument("--preview", action="store_true",
                    help="show the camera feed with detected landmarks drawn")
    args = ap.parse_args(argv)

    ok, why = handtrack.available()
    if not ok:
        print(f"Cannot calibrate: {why}")
        return 1
    clash = handtrack.opencv_conflict()
    if clash:
        print(f"WARNING: {len(clash)} OpenCV packages installed "
              f"({', '.join(clash)}). They overwrite each other. Do NOT just "
              f"uninstall one — that deletes files the survivor needs. Remove "
              f"them all and install one:\n  {handtrack.opencv_repair_command()}\n")
    import cv2
    import mediapipe as mp

    # Camera BEFORE model: the download is 7.5 MB and there is no point paying
    # for it to then discover there is nothing to point at.
    import config
    idx = args.device if args.device is not None else \
        getattr(config, "CAMERA_DEVICE_INDEX", 0)
    cap = cv2.VideoCapture(idx)
    if not cap.isOpened():
        # The webcam is exclusive, and a running Apex is the likeliest holder.
        print(f"Could not open camera {idx}. If Apex is running with hand "
              f"tracking on, it is holding the camera — quit Apex and try "
              f"again, or ask it to release the camera first.")
        return 1

    if handtrack.ensure_model() is None:
        cap.release()
        print("Cannot calibrate: the hand model could not be downloaded.")
        return 1

    # Same delegate and confidence the tracker will use — calibrating against
    # different settings than you run with would measure the wrong thing.
    landmarker, delegate_used, note = handtrack.build_landmarker(num_hands=1)
    if note:
        print(f"  {note}")
    print(f"  Inference on {delegate_used}.")

    try:
        print("=" * 62)
        print("  Pinch calibration — measuring HANDTRACK_PINCH_RATIO")
        print("=" * 62)
        print("\nKeep one hand in frame, palm toward the camera, throughout.")

        countdown("1/2  Hold your hand OPEN — fingers spread, thumb away "
                  "from the index finger.")
        open_samples, n, stats_open = collect(
            landmarker, cap, mp, args.seconds, 0, args.preview)

        countdown("2/2  Now PINCH — thumb and index fingertip touching — "
                  "and hold it there.")
        pinch_samples, _, stats_pinch = collect(
            landmarker, cap, mp, args.seconds, n, args.preview)
    finally:
        cap.release()
        landmarker.close()
        if args.preview:
            try:
                import cv2 as _c
                _c.destroyAllWindows()
            except Exception:
                pass

    print("\nWhat was measured:")
    print(f"  camera : {stats_open['frames'] + stats_pinch['frames']} frames "
          f"delivered, {stats_open['dropped'] + stats_pinch['dropped']} dropped")
    print(f"  hands  : {stats_open['hands'] + stats_pinch['hands']} detections")
    print(describe("open   ", open_samples))
    print(describe("pinched", pinch_samples))

    if not clean(open_samples) and not clean(pinch_samples):
        print(f"\nNo threshold recommended.\n  "
              f"{explain_no_readings(stats_open, stats_pinch)}")
        return 2

    value, reason = recommend_threshold(open_samples, pinch_samples)
    print()
    if value is None:
        print(f"No threshold recommended.\n  {reason}")
        return 2
    print(f"Recommended HANDTRACK_PINCH_RATIO = {value}\n  {reason}")

    if not args.write:
        answer = input("\nWrite this to .env now? (y/N): ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Not written. Set it yourself with:\n"
                  f"  python scripts/set_env_key.py HANDTRACK_PINCH_RATIO {value}")
            return 0

    from scripts.set_env_key import set_key
    what = set_key(Path(".env"), "HANDTRACK_PINCH_RATIO", str(value))
    print(f"[env] HANDTRACK_PINCH_RATIO {what} in .env — restart Apex to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
