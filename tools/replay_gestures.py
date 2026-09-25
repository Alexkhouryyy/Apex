"""Replay a gesture recording through the real tracker, board and recogniser.

    python -m tools.replay_gestures recordings/gestures-20260925-213000.json.gz

agent/gesture_recorder.py records the hand joints (no picture) while the
user does each gesture on cue. This feeds every frame, with its original
timing, through the SAME steps the live tracker runs — HandTracker._read_hands
(smoothing, 3D pinch, fist, latch), Board.apply_hands (grab, tap, throw) and
GestureRecognizer.feed_cursors (swipes) — and reports, per take, what fired
against what the prompt asked for. It uses whatever thresholds are set now, so
a change can be judged on the recording before anyone tries it by hand.

Each take is judged on its own fresh tracker and board, with a card under the
hand's first position, so one take's leftovers never count against the next.
"""
from __future__ import annotations

import argparse
import sys
import threading
from types import SimpleNamespace
from typing import Optional

# What each take should produce: (check, description). A check gets the
# take's observations and returns True (as asked), False (wrong) or None
# (nothing to judge — no hand was seen).
def _share(obs, key):
    n = obs["frames_with_hands"]
    return None if n == 0 else obs[key] / n


EXPECT = {
    "open":       (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") == 0, "never pinched"),
    "relaxed":    (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") == 0, "never pinched"),
    "side_on":    (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") == 0, "never pinched"),
    "fist":       (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") == 0, "never pinched (a fist)"),
    "idle":       (lambda o: None if not o["frames_with_hands"] else o["grabs"] == 0, "no grab"),
    "open_palm":  (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") == 0, "never pinched"),
    "pinch_hold": (lambda o: None if _share(o, "pinched") is None else _share(o, "pinched") >= 0.7, "pinched most of the time"),
    "grab_move":  (lambda o: None if not o["frames_with_hands"] else o["grabs"] >= 1, "grabbed"),
    "two_hands":  (lambda o: None if not o["frames_with_hands"] else o["two_hand"] >= 1, "both hands held it"),
}
for _n in (1, 2, 3):
    EXPECT[f"tap_{_n}"] = (lambda o: None if not o["frames_with_hands"] else o["taps"] == 1, "exactly one tap")
for _dir in ("up", "down", "left", "right"):
    for _n in (1, 2):
        EXPECT[f"swipe_{_dir}_{_n}"] = (
            (lambda d: lambda o: None if not o["frames_with_hands"] else f"swipe_{d}" in o["gestures"])(_dir),
            f"swipe_{_dir} recognised")


def _pts(rows):
    return [SimpleNamespace(x=r[0], y=r[1], z=r[2]) for r in rows]


def _result(frame):
    hands = frame["hands"]
    return SimpleNamespace(
        hand_landmarks=[_pts(h["lm"]) for h in hands],
        hand_world_landmarks=[_pts(h["world"]) if h.get("world") else None for h in hands],
        handedness=[[SimpleNamespace(category_name=h.get("side", "?"))] for h in hands])


def replay_take(frames: list) -> dict:
    """Run one take's frames through tracker -> board -> recogniser."""
    from agent import handtrack
    from agent.board import Board
    from agent.gestures import GestureRecognizer
    t = handtrack.HandTracker.__new__(handtrack.HandTracker)
    t._lock = threading.Lock()
    board = Board()
    board.persist = False
    rec = GestureRecognizer()
    obs = {"frames": len(frames), "frames_with_hands": 0, "pinched": 0, "fist": 0,
           "grabs": 0, "two_hand": 0, "taps": 0, "gestures": [], "ratios": []}
    placed = False
    was_held = False
    for f in frames:
        now = f["t"]
        cursors, details = t._read_hands(_result(f), now)
        if cursors and not placed:
            # Something to grab, under where the hand first appears.
            board.add("card", "Target", x=cursors[0][0], y=cursors[0][1])
            placed = True
        board.apply_hands(cursors, now=now)
        for g in rec.feed_cursors(cursors, now):
            obs["gestures"].append(g)
        if details:
            obs["frames_with_hands"] += 1
            obs["pinched"] += any(d["pinched"] for d in details)
            obs["fist"] += any(d.get("fist") for d in details)
            obs["ratios"] += [d["ratio"] for d in details if d.get("ratio") is not None]
        held = any(c["held"] for c in board.cards())
        if held and not was_held:
            obs["grabs"] += 1
        was_held = held
        obs["two_hand"] += any(c.get("hands") == 2 for c in board.cards())
    obs["taps"] = sum(1 for e in board.events_since(0) if e["type"] == "tapped")
    return obs


def replay(doc: dict) -> list[dict]:
    takes: dict = {}
    for f in doc.get("frames", []):
        takes.setdefault(f["take"], []).append(f)
    rows = []
    for spec in doc.get("script", []):
        take = spec["take"]
        obs = replay_take(takes.get(take, []))
        check, wanted = EXPECT.get(take, (lambda o: None, "—"))
        rows.append({"take": take, "wanted": wanted, "ok": check(obs), **obs})
    return rows


def suggest_pinch(rows) -> tuple[Optional[float], str]:
    """The pinch threshold these recordings support, from the calibrator's
    tested maths: everything that must NOT pinch vs the held pinch."""
    from scripts.calibrate_pinch import recommend_threshold
    not_pinch = [r for row in rows if row["take"] in ("open", "relaxed", "side_on", "open_palm")
                 for r in row["ratios"]]
    pinch = [r for row in rows if row["take"] == "pinch_hold" for r in row["ratios"]]
    return recommend_threshold(not_pinch, pinch)


def report(rows) -> str:
    lines = [f"{'take':<15} {'wanted':<26} {'result':<8} hands  pinched  fist  grabs  taps  gestures"]
    for r in rows:
        mark = {True: "OK", False: "WRONG", None: "no hand"}[r["ok"]]
        share = lambda k: f"{r[k] / r['frames_with_hands']:.0%}" if r["frames_with_hands"] else "—"
        lines.append(f"{r['take']:<15} {r['wanted']:<26} {mark:<8} {r['frames_with_hands']:>5}  "
                     f"{share('pinched'):>7}  {share('fist'):>4}  {r['grabs']:>5}  {r['taps']:>4}  "
                     f"{','.join(r['gestures']) or '-'}")
    judged = [r for r in rows if r["ok"] is not None]
    lines.append(f"\n{sum(r['ok'] for r in judged)}/{len(judged)} takes as asked "
                 f"({len(rows) - len(judged)} with no hand seen).")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("recording")
    args = ap.parse_args(argv)
    from agent import gesture_recorder
    rows = replay(gesture_recorder.load(args.recording))
    print(report(rows))
    value, why = suggest_pinch(rows)
    print(f"\nPinch threshold these recordings support: {value if value is not None else 'none'} — {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
