"""Gesture recognition — deliberately blind to where the hand positions came from.

`agent/handtrack.py` (Apex's own MediaPipe tracker, reading the webcam
directly) hands this a list of `(x, y, pinched)` per hand, in window fractions.
That is the entire contract this module needs — it never touches a camera, a
socket, or a clock of its own, which is why it is pure and cheaply testable
with recorded frame tables rather than a live hand.

An earlier version of Apex also polled a separate `barehands` server (MediaPipe
running inside a Chrome tab) as a second source feeding the same contract —
removed once the native tracker made it redundant, and because it never
reliably tracked hands in practice. This module's contract-first design is
what made that removal a deletion, not a rewrite: nothing here knew or cared
which source was talking.

## What this has to get right, and why

**Hand identity is not array position.** Whatever the source, hands arrive as an
ordered list whose order carries no identity. A hand resting at x=0.2 with a
second entering at x=0.8 can make `cursors[0]` jump the width of the screen in
one frame — a textbook swipe from a hand that never moved. Handled twice over:
pairing is by proximity with a `MAX_STEP` veto, and any change in hand count
resets every accumulator, so a gesture must be observed entirely within a
stretch of constant hand count.

**A missing observation is not an observation of stillness.** `feed_cursors`
takes `None` for "no data this tick" — a dead camera, a frozen browser tab, a
dropped frame. That is different from `[]`, which means "the source is working
and nobody's hands are up". Collapsing the two is how a frozen tracker becomes a
hand held perfectly still, and then a `pinch_hold` that never happened.

**Recognition and action are separate gates.** A recognized gesture is always
reported. Whether it is allowed to *do* anything is `gesture_action`'s business.
Otherwise a misconfigured allowlist and a broken recognizer look identical from
outside, which is the shape this codebase has produced eighteen times.
"""
from __future__ import annotations

from typing import Optional

import config

# ── Tuning ───────────────────────────────────────────────────────────────────
# Window fractions and seconds throughout, so every threshold survives a change
# of camera or screen size.

# Largest plausible movement between two observations. Beyond it, two samples
# are not the same hand — they are a renumbering. At 20 Hz, 0.35 of the window
# in 50 ms is about seven screen-widths a second: not a hand.
MAX_STEP = 0.35

# Below this, movement is tracking noise rather than intent.
NOISE_FLOOR = 0.02

# Wave: horizontal direction reversals inside a window. A natural wave runs
# 2-3 Hz; at 20 Hz that is ~6.7 samples per cycle, which survives. At 10 Hz a
# 3 Hz wave aliases away entirely — which is why sources poll at 20 Hz.
WAVE_WINDOW = 1.5
WAVE_MIN_REVERSALS = 3
WAVE_MIN_TRAVEL = 0.15
WAVE_MAX_HZ = 3.0              # a declared bound, not a pretence of having none

# Swipe: a decisive directional throw of the hand.
SWIPE_WINDOW = 0.5
SWIPE_MIN_TRAVEL = 0.35
SWIPE_DOMINANCE = 2.0          # the main axis must beat the other by this much

# Pinch-hold: held closed, roughly in place.
PINCH_HOLD_SECONDS = 1.2
PINCH_HOLD_RADIUS = 0.08

GESTURES = (
    "hands_present", "hands_gone", "wave", "pinch_hold",
    "swipe_left", "swipe_right", "swipe_up", "swipe_down",
)

# One hand's reading: x, y (window fractions, y grows downward), pinched.
Cursor = tuple


class _Track:
    """One hand's recent history, within a stretch of constant hand count."""

    __slots__ = ("samples", "pinch_start", "pinch_anchor")

    def __init__(self) -> None:
        self.samples: list[tuple[float, float, float]] = []  # ts, x, y
        self.pinch_start: Optional[float] = None
        self.pinch_anchor: Optional[tuple[float, float]] = None

    def trim(self, now: float, keep: float) -> None:
        cutoff = now - keep
        self.samples = [s for s in self.samples if s[0] >= cutoff]


class GestureRecognizer:
    """Turns a stream of hand readings into named gestures.

    Pure in the sense that matters: no sockets, no camera, no clock of its own.
    Every call takes the readings and the time, so a recorded session replays
    exactly and a test needs neither a webcam nor a browser.
    """

    def __init__(self, *, cooldown_seconds: Optional[float] = None) -> None:
        self.cooldown_seconds = (
            cooldown_seconds if cooldown_seconds is not None
            else getattr(config, "HANDTRACK_GESTURE_COOLDOWN_SECONDS", 3.0)
        )
        self._tracks: list[_Track] = []
        self._hand_count = 0
        self._last_emit: dict[str, float] = {}
        self._announced_present = False

    def reset(self) -> None:
        self._tracks = []
        self._hand_count = 0

    # -- the entry point ---------------------------------------------------
    def feed_cursors(self, cursors: Optional[list], now: float) -> list[str]:
        """Absorb one observation. Returns the gestures recognized on it.

        `None` means the source had nothing to report this tick — camera down,
        browser frozen, frame dropped. That is NOT the same as `[]`, which means
        the source is fine and no hands are up. Treating a missing observation
        as a still hand is how a frozen source fabricates a `pinch_hold`.
        """
        if cursors is None:
            # No observation. Forget the tracks — continuity is gone either way
            # — but do NOT emit `hands_gone`, because we did not see hands leave.
            # `describe("hands_gone")` writes "hands left the camera" into the
            # awareness log, and that would be a plain falsehood when what
            # actually happened is that the camera stopped working. Apex would
            # be recording an event about the user that never occurred.
            # `_announced_present` still clears, so hands returning correctly
            # re-announces themselves.
            self.reset()
            self._announced_present = False
            return []
        if not cursors:
            # An observation OF nothing: the source is fine and the hands are
            # down. That genuinely is "hands left the camera".
            self.reset()
            return self._gone_if_needed(now)

        fired: list[str] = []
        if not self._announced_present:
            self._announced_present = True
            fired += self._emit(["hands_present"], now)

        # The load-bearing half of the identity problem. A gesture must be
        # observed entirely within a stretch of constant hand count; anything
        # else risks reading a renumbering as motion. Blunt, and it kills the
        # whole class.
        if len(cursors) != self._hand_count:
            self._hand_count = len(cursors)
            self._tracks = [_Track() for _ in cursors]
            for tr, (x, y, p) in zip(self._tracks, cursors):
                tr.samples.append((now, x, y))
                if p:
                    tr.pinch_start, tr.pinch_anchor = now, (x, y)
            return fired

        order = self._pair(cursors)
        for tr_idx, cur_idx in enumerate(order):
            tr = self._tracks[tr_idx]
            if cur_idx is None:
                # No plausible continuation for this hand — treat it as broken
                # rather than teleported.
                tr.samples.clear()
                tr.pinch_start = tr.pinch_anchor = None
                continue
            x, y, pinched = cursors[cur_idx]
            tr.samples.append((now, x, y))
            tr.trim(now, max(WAVE_WINDOW, SWIPE_WINDOW) + 0.5)
            if pinched:
                if tr.pinch_start is None:
                    tr.pinch_start, tr.pinch_anchor = now, (x, y)
            else:
                tr.pinch_start = tr.pinch_anchor = None

            fired += self._emit(self._gestures_for(tr, now, pinched), now)

        return fired

    def _gone_if_needed(self, now: float) -> list[str]:
        if self._announced_present:
            self._announced_present = False
            return self._emit(["hands_gone"], now)
        return []

    # -- tracking ----------------------------------------------------------
    def _pair(self, cursors: list) -> list[Optional[int]]:
        """Match this observation's hands to existing tracks by proximity.

        List order carries no identity, so pairing on it is what produces
        phantom swipes. Greedy nearest-neighbour is enough at one or two hands,
        and MAX_STEP is the veto: beyond it, the two samples are not the same
        hand.
        """
        taken: set[int] = set()
        out: list[Optional[int]] = []
        # Tracks with history claim their nearest hand first; empty tracks then
        # adopt whatever is left. Order matters — the other way round lets a
        # track with no continuity claim steal a real hand's reading.
        empty: list[int] = []
        for slot, tr in enumerate(self._tracks):
            if not tr.samples:
                # A cleared track has no continuity claim, so it restarts from
                # any unclaimed hand. Returning None here instead would be a
                # trap door: the None branch clears the samples again, so a
                # single tracking glitch would kill that hand for the rest of
                # the session and gestures would just stop working.
                out.append(None)
                empty.append(slot)
                continue
            _, lx, ly = tr.samples[-1]
            best, best_d = None, MAX_STEP
            for i, (x, y, _p) in enumerate(cursors):
                if i in taken:
                    continue
                d = ((x - lx) ** 2 + (y - ly) ** 2) ** 0.5
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                taken.add(best)
            out.append(best)

        free = [i for i in range(len(cursors)) if i not in taken]
        for slot in empty:
            if free:
                out[slot] = free.pop(0)
        return out

    # -- gestures ----------------------------------------------------------
    def _gestures_for(self, tr: _Track, now: float, pinched: bool) -> list[str]:
        found: list[str] = []
        if pinched:
            if self._is_pinch_hold(tr, now):
                found.append("pinch_hold")
            # A pinched hand is carrying something; swipes and waves are for
            # open hands, so stop here.
            return found
        swipe = self._swipe(tr, now)
        if swipe:
            found.append(swipe)
        if self._is_wave(tr, now):
            found.append("wave")
        return found

    def _is_pinch_hold(self, tr: _Track, now: float) -> bool:
        if tr.pinch_start is None or tr.pinch_anchor is None:
            return False
        if now - tr.pinch_start < PINCH_HOLD_SECONDS:
            return False
        ax, ay = tr.pinch_anchor
        _, x, y = tr.samples[-1]
        return ((x - ax) ** 2 + (y - ay) ** 2) ** 0.5 <= PINCH_HOLD_RADIUS

    def _swipe(self, tr: _Track, now: float) -> Optional[str]:
        window = [s for s in tr.samples if s[0] >= now - SWIPE_WINDOW]
        if len(window) < 2:
            return None
        _, x0, y0 = window[0]
        _, x1, y1 = window[-1]
        dx, dy = x1 - x0, y1 - y0
        adx, ady = abs(dx), abs(dy)
        if adx >= SWIPE_MIN_TRAVEL and adx >= ady * SWIPE_DOMINANCE:
            return "swipe_right" if dx > 0 else "swipe_left"
        if ady >= SWIPE_MIN_TRAVEL and ady >= adx * SWIPE_DOMINANCE:
            # y grows downward in window fractions.
            return "swipe_down" if dy > 0 else "swipe_up"
        return None

    def _is_wave(self, tr: _Track, now: float) -> bool:
        window = [s for s in tr.samples if s[0] >= now - WAVE_WINDOW]
        if len(window) < WAVE_MIN_REVERSALS + 1:
            return False
        reversals, travel, direction = 0, 0.0, 0
        for (_, xa, _ya), (_, xb, _yb) in zip(window, window[1:]):
            dx = xb - xa
            if abs(dx) < NOISE_FLOOR:
                continue
            travel += abs(dx)
            step = 1 if dx > 0 else -1
            if direction and step != direction:
                reversals += 1
            direction = step
        return reversals >= WAVE_MIN_REVERSALS and travel >= WAVE_MIN_TRAVEL

    def _emit(self, names: list[str], now: float) -> list[str]:
        """Apply the per-gesture cooldown. Nothing else gates recognition."""
        out = []
        for name in names:
            last = self._last_emit.get(name, 0.0)
            if now - last < self.cooldown_seconds:
                continue
            self._last_emit[name] = now
            out.append(name)
        return out


def gesture_action(gesture: str) -> Optional[str]:
    """What this gesture is allowed to do, per HANDTRACK_GESTURE_ACTIONS.

    Deny-by-default: an unmapped gesture returns None and does nothing. It is
    still logged — recognition and action are separate gates, so "I waved and
    nothing happened" stays diagnosable from the live feed.
    """
    for entry in getattr(config, "HANDTRACK_GESTURE_ACTIONS", []) or []:
        name, _, action = str(entry).partition(":")
        if name.strip() == gesture and action.strip():
            return action.strip()
    return None


def describe(gesture: str) -> str:
    """The awareness-log line for a gesture."""
    action = gesture_action(gesture)
    if gesture == "hands_present":
        return "hands are up in front of the camera"
    if gesture == "hands_gone":
        return "hands left the camera"
    if action:
        return f"gesture: {gesture} → {action}"
    return f"gesture: {gesture} (not mapped to an action)"
