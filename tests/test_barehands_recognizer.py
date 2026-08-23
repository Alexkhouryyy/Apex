"""Gesture recognition from barehands' /state stream.

There is no camera here and there never will be in CI, so the split follows the
`tests/test_iot_watcher.py` doctrine: the recognizer is pure and gets exercised
exhaustively with frame tables, and the last hop — real fingers producing real
cursor frames in Chrome — stays unproven and is recorded as such in
docs/APEX_GAP_ANALYSIS.md rather than assumed.

Most of these exist because of a specific way barehands' protocol lies to you:

  * cursor index is MediaPipe's array position, not a hand identity, so a second
    hand appearing can renumber the first and forge a swipe;
  * the server keeps no timestamp and the page stops pushing when its tab is
    backgrounded, so a frozen hand and a held hand are byte-identical;
  * `d` is hardcoded to 0, so anything keyed on it silently never fires.
"""
import json

import pytest

import config
from agent import barehands_watcher as bh


def _body(cursors, items=None) -> bytes:
    """One /state payload, in the shape stage.html actually posts."""
    return json.dumps({
        "cursors": [{"x": x, "y": y, "p": 1 if p else 0, "d": 0}
                    for (x, y, p) in cursors],
        "items": items if items is not None else [],
    }).encode()


def _feed(rec, frames, start=1000.0, dt=0.05):
    """Play a list of cursor-lists through the recognizer at a fixed rate."""
    seen = []
    for i, cursors in enumerate(frames):
        seen += rec.feed(_body(cursors), start + i * dt)
    return seen


@pytest.fixture(autouse=True)
def no_cooldown(monkeypatch):
    """Cooldown is a separate concern; it has its own tests."""
    monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS",
                        ["wave:wake", "pinch_hold:listen", "swipe_down:stop"],
                        raising=False)


# ── Parsing ──────────────────────────────────────────────────────────────────

class TestParsing:
    @pytest.mark.parametrize("raw", [
        b"", b"not json", b"[]", b"null", b'"a string"', b"123",
    ])
    def test_garbage_is_dropped_not_raised(self, raw):
        """This runs in a poll thread — a raise kills hand tracking silently."""
        assert bh.parse_state(raw) is None

    def test_empty_object_is_a_real_state_not_an_error(self):
        """barehands stores b'{}' until the first tracker heartbeat, so this
        means 'server up, no Chrome tab' — a different thing from 'no hands'."""
        assert bh.parse_state(b"{}") == {}

    @pytest.mark.parametrize("cursors", [
        [{"x": "nope", "y": 0.1}],
        [{"x": None, "y": None}],
        ["not a dict"],
        [{}],
    ])
    def test_malformed_cursors_do_not_raise(self, cursors):
        bh.read_cursors({"cursors": cursors})

    def test_the_dead_d_field_is_never_read(self):
        """`d` is hardcoded to 0 in stage.html's pushState. Anything keyed on it
        silently never fires, and looks exactly like a hand that never moved.

        The assertion has to be one where reading `d` would CHANGE the answer:
        a swipe performed with d=1 and p=0. If `d` leaked into the pinch flag
        the swipe would be suppressed, because a pinched hand is carrying
        something and does not swipe. An earlier version of this test fed a
        single frame and passed with `d` wired straight in — it proved nothing.
        """
        def swipe(d):
            rec = bh.GestureRecognizer(cooldown_seconds=0)
            seen = []
            for i in range(16):
                body = json.dumps({"cursors": [
                    {"x": 0.1 + i * 0.05, "y": 0.5, "p": 0, "d": d}],
                    "items": []}).encode()
                seen += rec.feed(body, 1000.0 + i * 0.05)
            return seen

        assert "swipe_right" in swipe(0)
        assert swipe(1) == swipe(0)


# ── Tracker liveness: four states, not two ───────────────────────────────────

class TestTrackerStates:
    def test_unreachable_server_is_down(self):
        rec = bh.GestureRecognizer()
        rec.feed(None, 1000.0)
        assert rec.tracker == bh.TRACKER_DOWN
        assert "isn't running" in rec.tracker_note()

    def test_server_up_with_no_stage_is_its_own_state(self):
        """`{}` must not read as 'tracker present, no hands' — the difference
        is whether opening Chrome would fix it."""
        rec = bh.GestureRecognizer()
        rec.feed(b"{}", 1000.0)
        assert rec.tracker == bh.TRACKER_NO_STAGE
        assert "no stage is open" in rec.tracker_note()

    def test_pushing_frames_is_live(self):
        rec = bh.GestureRecognizer()
        rec.feed(_body([(0.5, 0.5, False)]), 1000.0)
        assert rec.tracker == bh.TRACKER_LIVE

    def test_protocol_change_is_announced_not_swallowed(self, capsys):
        """If barehands renames `cursors`, `.get("cursors", [])` returns [] for
        ever and is indistinguishable from 'you are not waving'."""
        rec = bh.GestureRecognizer()
        rec.feed(json.dumps({"items": [], "fingers": []}).encode(), 1000.0)
        out = capsys.readouterr().out
        assert "no 'cursors' key" in out
        assert "failed:" in out, "must trip smoke.py's no_silent_failures"


# ── Trap 2: the frozen tab ───────────────────────────────────────────────────

class TestStaleness:
    def test_frozen_hand_does_not_become_a_held_pinch(self):
        """THE regression test for the staleness guard.

        A pinched hand whose bytes never change is a backgrounded Chrome tab,
        not a hand being held still: real hands jitter and the coordinates carry
        four decimals. Remove the guard in feed() and pinch_hold fires here.
        """
        rec = bh.GestureRecognizer(stale_seconds=2.0, cooldown_seconds=0)
        frozen = _body([(0.5, 0.5, True)])
        fired = []
        for i in range(200):                      # 10 s at 20 Hz
            fired += rec.feed(frozen, 1000.0 + i * 0.05)
        assert "pinch_hold" not in fired
        assert rec.tracker == bh.TRACKER_FROZEN

    def test_a_real_held_pinch_still_fires(self):
        """The guard must not cost us the gesture it protects — jitter is the
        difference, and a real hand always has some."""
        rec = bh.GestureRecognizer(stale_seconds=2.0, cooldown_seconds=0)
        fired = []
        for i in range(60):                       # 3 s at 20 Hz
            x = 0.5 + (i % 3) * 0.001             # sub-noise-floor tremor
            fired += rec.feed(_body([(x, 0.5, True)]), 1000.0 + i * 0.05)
        assert "pinch_hold" in fired

    def test_identical_bytes_with_no_hands_is_legitimate(self):
        """An empty board with nobody's hands up is identical for ever and
        that is fine — the staleness rule has to be asymmetric."""
        rec = bh.GestureRecognizer(stale_seconds=2.0, cooldown_seconds=0)
        idle = _body([])
        for i in range(200):
            rec.feed(idle, 1000.0 + i * 0.05)
        assert rec.tracker == bh.TRACKER_LIVE, "an idle board is not a dead one"

    def test_frozen_then_moving_recovers(self):
        rec = bh.GestureRecognizer(stale_seconds=1.0, cooldown_seconds=0)
        frozen = _body([(0.5, 0.5, False)])
        for i in range(60):
            rec.feed(frozen, 1000.0 + i * 0.05)
        assert rec.tracker == bh.TRACKER_FROZEN
        rec.feed(_body([(0.6, 0.5, False)]), 1100.0)
        assert rec.tracker == bh.TRACKER_LIVE


# ── Trap 1: cursor index is not hand identity ────────────────────────────────

class TestHandIdentity:
    def test_a_second_hand_does_not_forge_a_swipe(self):
        """THE regression test for the hand-count reset.

        One hand rests at x=0.2. A second hand enters at x=0.8, and MediaPipe's
        detection order puts it at index 0 — so cursors[0] jumps 0.2 -> 0.8 in
        one frame. Naively that is a full-screen swipe from a hand that never
        moved. Remove the hand-count reset in _advance() and this fires.
        """
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.2, 0.5, False)]] * 10
        frames += [[(0.8, 0.5, False), (0.2, 0.5, False)]] * 10
        fired = _feed(rec, frames)
        assert not [g for g in fired if g.startswith("swipe")], \
            f"a renumbered hand produced {fired}"

    def test_a_hand_leaving_does_not_forge_a_swipe(self):
        """The mirror case: two hands, the left one leaves, the survivor
        renumbers from index 1 to index 0."""
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.1, 0.5, False), (0.9, 0.5, False)]] * 10
        frames += [[(0.9, 0.5, False)]] * 10
        fired = _feed(rec, frames)
        assert not [g for g in fired if g.startswith("swipe")]

    def test_a_tracking_glitch_does_not_kill_the_hand_for_ever(self):
        """A track that matches nothing has its history cleared. If the pairing
        then refused to give an empty track a cursor, the clear would re-run
        every frame and that hand would be dead for the rest of the session —
        gestures would simply stop working, with nothing logged. Found by the
        cooldown test failing for the wrong reason; pinned here on purpose.
        """
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        _feed(rec, [[(0.05, 0.5, False)]] * 4, start=1000.0)
        _feed(rec, [[(0.95, 0.5, False)]] * 2, start=1000.2)   # the glitch
        after = _feed(rec, [[(0.1 + i * 0.05, 0.5, False)] for i in range(16)],
                      start=1001.0)
        assert "swipe_right" in after, "the hand never recovered from one glitch"

    def test_a_teleporting_cursor_is_not_tracked_through(self):
        """Beyond MAX_STEP in one frame, two samples are not the same hand."""
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.05, 0.5, False)]] * 5 + [[(0.95, 0.5, False)]] * 5
        fired = _feed(rec, frames)
        assert not [g for g in fired if g.startswith("swipe")]


# ── The gestures themselves ──────────────────────────────────────────────────

class TestGestures:
    def test_swipe_right(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.1 + i * 0.05, 0.5, False)] for i in range(16)]
        assert "swipe_right" in _feed(rec, frames)

    def test_swipe_left(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.9 - i * 0.05, 0.5, False)] for i in range(16)]
        assert "swipe_left" in _feed(rec, frames)

    def test_swipe_down_is_not_swipe_up(self):
        """y grows downward in window fractions — an inverted axis here would
        map 'dismiss' onto 'summon'."""
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.5, 0.1 + i * 0.05, False)] for i in range(16)]
        fired = _feed(rec, frames)
        assert "swipe_down" in fired and "swipe_up" not in fired

    def test_a_diagonal_drift_is_not_a_swipe(self):
        """Without the dominance rule, ambling diagonally across the screen
        fires two swipes at once."""
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.1 + i * 0.05, 0.1 + i * 0.05, False)] for i in range(16)]
        assert not [g for g in _feed(rec, frames) if g.startswith("swipe")]

    def test_slow_drift_is_not_a_swipe(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.1 + i * 0.005, 0.5, False)] for i in range(60)]
        assert not [g for g in _feed(rec, frames) if g.startswith("swipe")]

    def test_wave(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = []
        for cycle in range(4):                    # ~2.7 Hz at 20 Hz sampling
            for x in (0.40, 0.48, 0.56, 0.48):
                frames.append([(x, 0.5, False)])
        assert "wave" in _feed(rec, frames)

    def test_a_hand_held_still_is_not_a_wave(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.5 + (i % 2) * 0.001, 0.5, False)] for i in range(40)]
        assert "wave" not in _feed(rec, frames)

    def test_a_pinched_hand_does_not_wave_or_swipe(self):
        """A pinched hand is carrying something across the board. Reading that
        as a swipe would fire 'dismiss' every time you move a card."""
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        frames = [[(0.1 + i * 0.05, 0.5, True)] for i in range(16)]
        fired = _feed(rec, frames)
        assert not [g for g in fired if g.startswith("swipe") or g == "wave"]

    def test_hands_present_then_gone(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        fired = _feed(rec, [[(0.5, 0.5, False)]] * 5 + [[]] * 5)
        assert "hands_present" in fired and "hands_gone" in fired

    def test_hands_present_fires_once_not_every_frame(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0)
        fired = _feed(rec, [[(0.5, 0.5, False)]] * 40)
        assert fired.count("hands_present") == 1


# ── Cooldown ─────────────────────────────────────────────────────────────────

class TestCooldown:
    def test_the_same_gesture_does_not_repeat_within_the_cooldown(self):
        rec = bh.GestureRecognizer(cooldown_seconds=5.0)
        frames = [[(0.1 + i * 0.05, 0.5, False)] for i in range(16)]
        assert _feed(rec, frames).count("swipe_right") == 1

    def test_the_cooldown_expires(self):
        rec = bh.GestureRecognizer(cooldown_seconds=0.5)
        frames = [[(0.1 + i * 0.05, 0.5, False)] for i in range(16)]
        first = _feed(rec, frames, start=1000.0)
        second = _feed(rec, frames, start=2000.0)
        assert "swipe_right" in first and "swipe_right" in second


# ── Recognition and action are separate gates ────────────────────────────────

class TestActionAllowlist:
    def test_an_empty_allowlist_gives_every_gesture_no_power(self, monkeypatch):
        """Deny-by-default, matching what dfc5590 did to the channels."""
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS", [], raising=False)
        for g in bh.GESTURES:
            assert bh.gesture_action(g) is None

    def test_an_unmapped_gesture_is_still_described(self, monkeypatch):
        """THE design rule. A gesture always reaches the awareness log; only the
        ACTION is gated. Otherwise a misconfigured map and a broken recognizer
        look identical from outside — the shape behind eighteen findings."""
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS", [], raising=False)
        assert "swipe_left" in bh.describe("swipe_left")
        assert "not mapped" in bh.describe("swipe_left")

    def test_a_mapped_gesture_names_its_action(self, monkeypatch):
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS",
                            ["wave:wake"], raising=False)
        assert bh.gesture_action("wave") == "wake"
        assert "wake" in bh.describe("wave")

    @pytest.mark.parametrize("entry", ["wave", "wave:", ":wake", "", "   "])
    def test_malformed_allowlist_entries_grant_nothing(self, monkeypatch, entry):
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS",
                            [entry], raising=False)
        assert bh.gesture_action("wave") is None
