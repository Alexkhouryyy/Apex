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


# The recognizer, the thresholds and the action allowlist moved to
# agent/gestures.py when Apex grew its own MediaPipe tracker: none of it was
# ever barehands-specific, it only lived here because this was the first source
# of hands. Re-exported so existing callers and tests keep working.
from agent.gestures import (                                    # noqa: F401
    GESTURES, MAX_STEP, NOISE_FLOOR, PINCH_HOLD_RADIUS, PINCH_HOLD_SECONDS,
    SWIPE_DOMINANCE, SWIPE_MIN_TRAVEL, SWIPE_WINDOW, WAVE_MAX_HZ,
    WAVE_MIN_REVERSALS, WAVE_MIN_TRAVEL, WAVE_WINDOW,
    GestureRecognizer as _CoreRecognizer, describe, gesture_action,
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


class GestureRecognizer(_CoreRecognizer):
    """The shared recognizer plus everything specific to reading it over HTTP.

    The extra work here is entirely about barehands' protocol having no clock:
    `GET /state` replays the last POSTed bytes for ever, so this class decides
    whether a payload is a fresh observation at all before handing hands to the
    core. Apex's own tracker needs none of it — a camera that stops delivering
    frames says so.
    """

    def __init__(self, *, stale_seconds: Optional[float] = None,
                 cooldown_seconds: Optional[float] = None) -> None:
        super().__init__(cooldown_seconds=cooldown_seconds)
        self.stale_seconds = (
            stale_seconds if stale_seconds is not None
            else getattr(config, "BAREHANDS_STALE_SECONDS", 2.0)
        )
        self._last_body: Optional[bytes] = None
        self._unchanged_since: Optional[float] = None
        self._tracker = TRACKER_DOWN
        self._warned_shape = False

    @property
    def tracker(self) -> str:
        return self._tracker

    def tracker_note(self) -> str:
        return _TRACKER_HUMAN.get(self._tracker, self._tracker)

    def _reset_tracks(self) -> None:
        self.reset()

    def feed(self, body: Optional[bytes], now: float) -> list[str]:
        """Absorb one poll. Returns the gestures recognized on this frame.

        `body` is None when the server could not be reached at all.
        """
        if body is None:
            self._tracker = TRACKER_DOWN
            self._last_body = None
            self._unchanged_since = None
            return self.feed_cursors(None, now)

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
            return self.feed_cursors(None, now)

        # The protocol moved. `.get("cursors", [])` would return [] for ever and
        # be indistinguishable from "you are not waving", so say so out loud —
        # the wording carries a failure marker on purpose, so tools/smoke.py's
        # no_silent_failures catches it.
        if "cursors" not in state:
            if not self._warned_shape:
                self._warned_shape = True
                print("[Barehands] /state carries no 'cursors' key — the protocol "
                      "changed and hand tracking failed: nothing will be detected.")
            self._tracker = TRACKER_LIVE
            self.reset()
            return []

        cursors = read_cursors(state)
        if body == self._last_body:
            # An unchanged payload carries no new observation, so it is passed
            # to the core as None rather than as hands. This matters more than
            # the staleness timer: PINCH_HOLD_SECONDS (1.2) is shorter than
            # BAREHANDS_STALE_SECONDS (2.0), so advancing on repeats would fire
            # pinch_hold a full second before anything noticed the tab was
            # frozen. Not advancing removes the race instead of tuning two
            # constants against each other.
            if self._unchanged_since is None:
                self._unchanged_since = now
            if cursors and now - self._unchanged_since >= self.stale_seconds:
                # A live camera cannot produce four-decimal coordinates that
                # never move. The tab is backgrounded or gone.
                if self._tracker != TRACKER_FROZEN:
                    self._tracker = TRACKER_FROZEN
                    return self.feed_cursors(None, now)
            elif not cursors:
                # Legitimately identical for ever: nobody's hands are up and the
                # board is static. Nothing pending, nothing wrong.
                self._tracker = TRACKER_LIVE
            return []

        self._last_body = body
        self._unchanged_since = None
        self._tracker = TRACKER_LIVE
        return self.feed_cursors(cursors, now)


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
        # When Apex's own MediaPipe tracker is running, this watcher stays up
        # for the four tracker-liveness states that tools/barehands.py needs,
        # but stops emitting gestures. Two sources feeding one awareness log
        # would double every wave and make the cooldown meaningless.
        self.gestures_enabled = True
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

        if not self.gestures_enabled:
            return

        for g in gestures:
            # Always logged, whatever the allowlist says.
            self.log.add("gesture", describe(g))
            action = gesture_action(g)
            if action and self.on_gesture:
                try:
                    self.on_gesture(g, action)
                except Exception as e:
                    print(f"[Barehands] gesture handler error: {e}")
