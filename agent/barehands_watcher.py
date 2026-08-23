"""Hand tracking — Apex's eyes on your hands, via the barehands board.

`barehands` (AGPL-3.0) runs as its own process on 127.0.0.1:8794. A Chrome tab
owns the webcam and runs Google MediaPipe *in the browser*. No barehands code
lives here; Apex speaks HTTP to localhost, which is aggregation rather than
derivation, so Apex stays MIT.

## The finding this module is shaped around

barehands recognizes clap, claw, tap, throw and two-hand-scale **inside its own
page and exports none of them**. The only thing that crosses to localhost is the
tracker's scene heartbeat:

    GET /state -> {"cursors":[{"x":0.51,"y":0.33,"p":0,"d":0}], "items":[...]}

x/y are window fractions, `p` is 1 while pinched. So Apex can see your hands but
must derive its own gestures. Anyone who assumed barehands hands over gestures
would ship a recognizer wired to a field that never arrives.

## Four traps, all of them live

1. **Cursor index is not hand identity.** The page does
   `(res.landmarks||[]).forEach((lms, i) => getCursor(i))` — `i` is MediaPipe's
   positional array index. A hand at x=0.2 with a second hand entering at x=0.8
   can see `cursors[0]` jump 0.2 -> 0.8 in one frame because the *new* hand took
   index 0. That is a textbook swipe from a hand that never moved. Handled by
   pairing on proximity and by resetting every accumulator whenever the hand
   count changes.

2. **A backgrounded tab looks like a held pinch.** The server keeps no
   timestamp — `GET /state` replays the last POSTed bytes forever — and
   `requestAnimationFrame` stops when the tab is not in front. A hand frozen
   mid-pinch is byte-identical to a hand *being held* mid-pinch. Handled
   asymmetrically: identical bytes with no cursors is a legitimate idle board and
   may last forever; identical bytes *with* cursors cannot come from a live
   camera, because real hands jitter and coordinates carry four decimals.

3. **`d` is hardcoded to 0** in the page's pushState. It is a dead field and
   nothing here may read it.

4. **Recognition and action are separate gates.** A recognized gesture always
   reaches the awareness log; whether it *does* anything is the allowlist's
   business. Otherwise a misconfigured map and a broken recognizer look
   identical from outside, which is the failure shape this codebase has produced
   eighteen times.

The recognizer is module-level and pure, lifted out of the poll loop for the same
reason `iot_watcher.decide_event` was: a gesture engine that can only be
exercised by waving at a webcam is a gesture engine nothing ever tests.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Optional

import config

if TYPE_CHECKING:
    from agent.awareness import AwarenessLog


# ── Tracker liveness — four states, not two ──────────────────────────────────
# "Is barehands working?" has four answers and they need four different
# responses. Collapsing them to up/down is how "nothing happens when I wave"
# becomes unattributable.
TRACKER_DOWN = "down"          # nothing listening on the port
TRACKER_NO_STAGE = "no_stage"  # server up, no Chrome tab has ever connected
TRACKER_LIVE = "live"          # tracker is pushing frames
TRACKER_FROZEN = "frozen"      # bytes stopped changing — tab backgrounded/closed

_TRACKER_HUMAN = {
    TRACKER_DOWN: "the board is dark — the barehands server isn't running",
    TRACKER_NO_STAGE: "barehands is up, but no stage is open in Chrome yet",
    TRACKER_LIVE: "the stage is live",
    TRACKER_FROZEN: "the stage is frozen — that Chrome tab isn't in front",
}


# ── Tuning ───────────────────────────────────────────────────────────────────
# Window fractions and seconds throughout. Every threshold is stated in units
# that survive a change of screen size, because the source coordinates are.

# Largest plausible movement between two polls. Above this, two samples are not
# the same hand — they are a renumbering (trap 1). At 20 Hz, 0.35 of the window
# in 50 ms is roughly seven screen-widths a second: not a hand.
MAX_STEP = 0.35

# Below this, movement is tracking noise rather than intent.
NOISE_FLOOR = 0.02

# Wave: horizontal direction reversals inside a window. A natural wave runs
# 2-3 Hz; at 20 Hz that is ~6.7 samples per cycle, which survives. At 10 Hz a
# 3 Hz wave aliases away entirely — which is why the default poll is 20 Hz.
WAVE_WINDOW = 1.5
WAVE_MIN_REVERSALS = 3
WAVE_MIN_TRAVEL = 0.15
WAVE_MAX_HZ = 3.0              # declared bound, not a pretence of having none

# Swipe: a decisive directional throw of the hand.
SWIPE_WINDOW = 0.5
SWIPE_MIN_TRAVEL = 0.35
SWIPE_DOMINANCE = 2.0          # main axis must beat the other by this factor

# Pinch-hold: held closed, roughly in place.
PINCH_HOLD_SECONDS = 1.2
PINCH_HOLD_RADIUS = 0.08

GESTURES = (
    "hands_present", "hands_gone", "wave", "pinch_hold",
    "swipe_left", "swipe_right", "swipe_up", "swipe_down",
)


def parse_state(body: bytes) -> Optional[dict]:
    """Decode a /state body. Returns None for anything unusable.

    Returns `{}` unchanged — that is barehands' initial value and means "server
    up, no tracker has ever connected", which is a real state rather than an
    error.
    """
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_cursors(state: dict) -> list[tuple[float, float, bool]]:
    """Pull (x, y, pinched) out of a parsed /state payload.

    Deliberately never reads `d`: the page hardcodes it to 0, so a gesture keyed
    on it would never fire and would look exactly like a hand that never moved.
    """
    out: list[tuple[float, float, bool]] = []
    for c in state.get("cursors") or []:
        if not isinstance(c, dict):
            continue
        try:
            x = float(c.get("x", 0.0))
            y = float(c.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        out.append((x, y, bool(c.get("p"))))
    return out


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
    """Turns a stream of /state payloads into named gestures.

    Pure in the sense that matters: no sockets, no clock of its own. Every call
    takes the payload and the time, so a recorded session replays exactly.
    """

    def __init__(self, *, stale_seconds: Optional[float] = None,
                 cooldown_seconds: Optional[float] = None) -> None:
        self.stale_seconds = (
            stale_seconds if stale_seconds is not None
            else getattr(config, "BAREHANDS_STALE_SECONDS", 2.0)
        )
        self.cooldown_seconds = (
            cooldown_seconds if cooldown_seconds is not None
            else getattr(config, "BAREHANDS_GESTURE_COOLDOWN_SECONDS", 3.0)
        )
        self._tracks: list[_Track] = []
        self._hand_count = 0
        self._last_body: Optional[bytes] = None
        self._unchanged_since: Optional[float] = None
        self._last_emit: dict[str, float] = {}
        self._tracker = TRACKER_DOWN
        self._warned_shape = False
        self._announced_present = False

    # -- state -------------------------------------------------------------
    @property
    def tracker(self) -> str:
        return self._tracker

    def tracker_note(self) -> str:
        return _TRACKER_HUMAN.get(self._tracker, self._tracker)

    def _reset_tracks(self) -> None:
        self._tracks = []
        self._hand_count = 0

    # -- the gate ----------------------------------------------------------
    def feed(self, body: Optional[bytes], now: float) -> list[str]:
        """Absorb one poll. Returns the gestures recognized on this frame.

        `body` is None when the server could not be reached at all.
        """
        if body is None:
            changed = self._tracker != TRACKER_DOWN
            self._tracker = TRACKER_DOWN
            self._last_body = None
            self._unchanged_since = None
            self._reset_tracks()
            return self._gone_if_needed(now) if changed else []

        state = parse_state(body)
        if state is None:
            return []

        # Server up, nothing has ever pushed a frame. barehands stores b"{}"
        # until the first tracker heartbeat, so this is a distinct condition
        # from "tracker present but idle" and must not be read as no hands.
        if "cursors" not in state and "items" not in state:
            self._tracker = TRACKER_NO_STAGE
            self._last_body = body
            self._unchanged_since = None
            self._reset_tracks()
            return self._gone_if_needed(now)

        # The protocol moved. `.get("cursors", [])` would return [] forever and
        # be indistinguishable from "you are not waving", so say so out loud —
        # the wording carries a failure marker on purpose, so tools/smoke.py's
        # no_silent_failures catches it.
        if "cursors" not in state:
            if not self._warned_shape:
                self._warned_shape = True
                print("[Barehands] /state carries no 'cursors' key — the protocol "
                      "changed and hand tracking failed: nothing will be detected.")
            self._tracker = TRACKER_LIVE
            self._reset_tracks()
            return []

        # Trap 2. Identical bytes mean different things depending on whether
        # anyone's hands are up.
        cursors = read_cursors(state)
        if body == self._last_body:
            # An unchanged payload carries no new observation, so the tracks do
            # NOT advance on it. This matters more than the staleness timer:
            # advancing here would fabricate evidence — a pinch "held" across
            # frames that never arrived — and because PINCH_HOLD_SECONDS (1.2)
            # is shorter than BAREHANDS_STALE_SECONDS (2.0), a frozen tab would
            # fire pinch_hold a full second before anything noticed it was
            # frozen. Not advancing removes that race entirely instead of
            # tuning two constants against each other.
            if self._unchanged_since is None:
                self._unchanged_since = now
            if cursors and now - self._unchanged_since >= self.stale_seconds:
                # A live camera cannot produce four-decimal coordinates that
                # never move. The tab is backgrounded or gone.
                if self._tracker != TRACKER_FROZEN:
                    self._tracker = TRACKER_FROZEN
                    self._reset_tracks()
                    return self._gone_if_needed(now)
            elif not cursors:
                # Legitimately identical for ever: nobody's hands are up and the
                # board is static. Nothing pending, nothing wrong.
                self._tracker = TRACKER_LIVE
            return []

        self._last_body = body
        self._unchanged_since = None
        self._tracker = TRACKER_LIVE
        return self._advance(cursors, now)

    def _gone_if_needed(self, now: float) -> list[str]:
        if self._announced_present:
            self._announced_present = False
            return self._emit(["hands_gone"], now)
        return []

    # -- tracking ----------------------------------------------------------
    def _advance(self, cursors: list[tuple[float, float, bool]],
                 now: float) -> list[str]:
        if not cursors:
            self._reset_tracks()
            return self._gone_if_needed(now)

        fired: list[str] = []
        if not self._announced_present:
            self._announced_present = True
            fired += self._emit(["hands_present"], now)

        # Trap 1, the load-bearing half. A gesture must be observed entirely
        # within a stretch of constant hand count; anything else risks reading a
        # renumbering as motion. Blunt, and it kills the whole class.
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

    def _pair(self, cursors: list[tuple[float, float, bool]]) -> list[Optional[int]]:
        """Match this frame's cursors to existing tracks by proximity.

        Index order is MediaPipe's detection order and carries no identity, so
        pairing on it is what produces phantom swipes. Greedy nearest-neighbour
        is enough at one or two hands, and MAX_STEP is the veto: beyond it, the
        two samples are not the same hand.
        """
        taken: set[int] = set()
        out: list[Optional[int]] = []
        # Tracks with history claim their nearest cursor first; empty tracks
        # then adopt whatever is left. Order matters — doing it the other way
        # lets a track with no continuity claim steal a real hand's cursor.
        empty: list[int] = []
        for slot, tr in enumerate(self._tracks):
            if not tr.samples:
                # A cleared track has no continuity claim, so it restarts from
                # any unclaimed cursor. Returning None here instead would be a
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
    """What this gesture is allowed to do, per BAREHANDS_GESTURE_ACTIONS.

    Deny-by-default: an unmapped gesture returns None and does nothing. It is
    still logged — recognition and action are separate gates, so "I waved and
    nothing happened" stays diagnosable from the live feed.
    """
    for entry in getattr(config, "BAREHANDS_GESTURE_ACTIONS", []) or []:
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


class BarehandsWatcher(threading.Thread):
    """Polls barehands' /state and feeds recognized gestures into awareness.

    Follows IoTWatcher: daemon thread, threading.Event for both sleep and stop,
    exponential backoff, and every decision made by a pure function it can hand
    a payload to.
    """

    def __init__(self, log: "AwarenessLog", *, on_gesture=None,
                 url: Optional[str] = None, poll_hz: Optional[float] = None):
        super().__init__(daemon=True, name="BarehandsWatcher")
        self.log = log
        self.on_gesture = on_gesture
        self.url = (url or getattr(config, "BAREHANDS_URL", "")
                    or "http://127.0.0.1:8794").rstrip("/")
        hz = poll_hz or getattr(config, "BAREHANDS_POLL_HZ", 20.0)
        self.interval = 1.0 / max(1.0, float(hz))
        self.recognizer = GestureRecognizer()
        self._stop = threading.Event()
        self._last_tracker: Optional[str] = None

    def stop(self) -> None:
        self._stop.set()

    def fetch(self) -> Optional[bytes]:
        """One GET /state. None means the server could not be reached."""
        try:
            req = urllib.request.Request(f"{self.url}/state",
                                         headers={"User-Agent": "Apex"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.read()
        except Exception:
            return None

    def run(self) -> None:
        print(f"[Barehands] Watching {self.url} at {1 / self.interval:.0f} Hz.")
        while not self._stop.wait(timeout=self.interval):
            try:
                self._tick(time.time())
            except Exception as e:
                # This runs in a background thread. A raise here kills hand
                # tracking silently for the rest of the session.
                print(f"[Barehands] tick error: {e}")

    def _tick(self, now: float) -> None:
        body = self.fetch()
        gestures = self.recognizer.feed(body, now)

        tracker = self.recognizer.tracker
        if tracker != self._last_tracker:
            self._last_tracker = tracker
            print(f"[Barehands] {self.recognizer.tracker_note()}")

        for g in gestures:
            # Always logged, whatever the allowlist says.
            self.log.add("gesture", describe(g))
            action = gesture_action(g)
            if action and self.on_gesture:
                try:
                    self.on_gesture(g, action)
                except Exception as e:
                    print(f"[Barehands] gesture handler error: {e}")
