"""Apex's own glass board — cards and 3D models you move with your hands.

`barehands` does this too. What this has instead of a copy of their design is a
different architecture, and the difference is not cosmetic.

## Why Apex's board is not a barehands clone

barehands runs MediaPipe **in the browser**, because it has no backend to run it
in. That forces three things on it: a ~26 MB WASM download on first load, the
page competing with itself for the GPU, and — the one that actually hurts —
tracking that stops dead the moment the tab is not in front, because browsers
pause `requestAnimationFrame`. The whole staleness apparatus in
`agent/barehands_watcher.py` exists to detect exactly that.

Apex already tracks hands in Python (`agent/handtrack.py`). So here the browser
is a **dumb renderer**: it draws what Python sends and decides nothing. Tracking
survives a backgrounded tab, survives the page being closed, and shares one
camera with everything else Apex does.

That inverts one thing. The webcam is exclusive, so while Python holds it the
browser cannot open it for a video background — the frames travel the other way,
which is what `handtrack.latest_frame()` was already for.

## The interaction, and whose it is

One hand drags. **Two hands on the same object** scale it by the distance
between them and rotate it by the angle between them — the two-finger gesture
everyone already knows from a touchscreen, lifted to two hands. That is
deliberately not barehands' hold-still-to-rotate: theirs is their interaction
design, arrived at over weeks of tuning, and this one is discoverable without
being taught.

Everything here is pure state — no sockets, no rendering, no camera. The board
is a list of objects and the rules for moving them, and that is testable without
a browser, which is the half that would otherwise never be exercised.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Optional

# Objects live in window fractions, exactly like the cursor stream feeding them,
# so nothing has to know the display size and a resized window moves nothing.
CARD_W = 0.22
CARD_H = 0.16

# A pinch grabs the nearest object within this radius. Generous, because the
# alternative failure — reaching for something and getting nothing — reads as
# the tracking being broken rather than as a miss.
GRAB_RADIUS = 0.14

MAX_CARDS = 24

# Scaled to nothing an object cannot be grabbed again; scaled past the screen it
# cannot be seen. Both are one-way trips with no keyboard to undo them.
MIN_SCALE = 0.25
MAX_SCALE = 4.0


class Card:
    """One thing on the glass — a text card, an image, or a 3D model."""

    __slots__ = ("id", "kind", "title", "body", "src", "x", "y", "scale",
                 "rot", "held_by", "created")

    def __init__(self, kind: str, title: str, body: str = "",
                 x: float = 0.5, y: float = 0.5, src: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind          # card | model | image
        self.title = title
        self.body = body
        self.src = src            # jail-relative prop path (models and images)
        self.x, self.y = x, y
        self.scale = 1.0
        self.rot = 0.0            # radians about Y — models only
        # A LIST, not a single hand. Two hands on one object is what scaling and
        # rotating mean, and a single holder cannot express that.
        self.held_by: list = []
        self.created = time.time()

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "body": self.body, "src": self.src,
                "x": round(self.x, 4), "y": round(self.y, 4),
                "scale": round(self.scale, 3), "rot": round(self.rot, 4),
                "held": bool(self.held_by), "hands": len(self.held_by)}


class Board:
    """The objects, and what hands do to them.

    Thread-safe because the tracker thread drives it while the dashboard thread
    reads it — the same split every watcher in this codebase has.
    """

    def __init__(self, max_cards: int = MAX_CARDS):
        self._cards: list[Card] = []
        self._lock = threading.Lock()
        self._max = max_cards
        # Where on the object each hand took hold, so a drag moves it by the
        # hand's DELTA rather than snapping its centre to the fingertip.
        # Snapping looks like the object jumping into your hand.
        self._grab_offset: dict[int, tuple[float, float]] = {}
        # Per-object reference for a two-handed grab: the span, angle and size
        # at the moment the second hand joined. Scaling relative to that is what
        # stops the object snapping the instant the grab begins.
        self._pair_ref: dict = {}

    # -- content ----------------------------------------------------------
    def add(self, kind: str, title: str, body: str = "",
            x: float = 0.5, y: float = 0.35, src: str = "") -> Card:
        card = Card(kind, title, body, x, y, src)
        with self._lock:
            self._cards.append(card)
            # Oldest out first. A board that grows without limit becomes
            # unusable long before it becomes slow.
            while len(self._cards) > self._max:
                self._cards.pop(0)
        return card

    def clear(self) -> int:
        with self._lock:
            n = len(self._cards)
            self._cards.clear()
            self._grab_offset.clear()
            self._pair_ref.clear()
        return n

    def set_src(self, card_id: str, src: str) -> bool:
        """Point an existing card at a different prop file — used when a
        recolor produces a new export that must replace what the card shows,
        without disturbing its position, scale or rotation."""
        with self._lock:
            for c in self._cards:
                if c.id == card_id:
                    c.src = src
                    return True
        return False

    def remove(self, card_id: str) -> bool:
        with self._lock:
            for i, c in enumerate(self._cards):
                if c.id == card_id:
                    self._cards.pop(i)
                    self._pair_ref.pop(card_id, None)
                    return True
        return False

    def cards(self) -> list[dict]:
        with self._lock:
            return [c.as_dict() for c in self._cards]

    def count(self) -> int:
        with self._lock:
            return len(self._cards)

    # -- hands ------------------------------------------------------------
    @staticmethod
    def read_cursors(cursors) -> list:
        """Normalize the cursor list, dropping anything unusable.

        Malformed entries are skipped rather than raised on: this runs on the
        tracker thread, where an exception takes hand tracking down along with
        the board. KeyError is in the caught set because a dict subscripts by
        key, not position — `{}[0]` raises KeyError, not IndexError.
        """
        out = []
        for cur in (cursors or []):
            try:
                out.append((float(cur[0]), float(cur[1]), bool(cur[2])))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return out

    def apply_hands(self, cursors) -> None:
        """Move, scale and rotate according to this frame's hands.

        `cursors` is the same `(x, y, pinched)` shape the recognizer takes, so
        the board and the gesture engine read one stream rather than two that
        could disagree about where your hand is.
        """
        hands = self.read_cursors(cursors)
        if not hands:
            # Hands gone: release everything. Without this an object stays stuck
            # to a hand that left the frame, and the only way to free it is to
            # reach back to exactly where it was.
            with self._lock:
                for c in self._cards:
                    c.held_by = []
                self._grab_offset.clear()
                self._pair_ref.clear()
            return

        with self._lock:
            # Drop holds whose hand let go or vanished from the frame.
            for c in self._cards:
                kept = [i for i in c.held_by if i < len(hands) and hands[i][2]]
                if len(kept) != len(c.held_by):
                    # The pair changed, so the two-handed reference is stale.
                    self._pair_ref.pop(c.id, None)
                c.held_by = kept

            for idx, (hx, hy, pinched) in enumerate(hands):
                if not pinched:
                    self._grab_offset.pop(idx, None)
                    continue
                if any(idx in c.held_by for c in self._cards):
                    continue
                target = self._nearest(hx, hy, idx)
                if target is None:
                    continue
                # A second hand may join something already held — that is how a
                # two-handed grab begins.
                if len(target.held_by) < 2:
                    target.held_by.append(idx)
                    self._grab_offset[idx] = (target.x - hx, target.y - hy)
                    if len(target.held_by) == 2:
                        self._pair_ref.pop(target.id, None)

            for c in self._cards:
                if len(c.held_by) == 1:
                    idx = c.held_by[0]
                    hx, hy, _ = hands[idx]
                    ox, oy = self._grab_offset.get(idx, (0.0, 0.0))
                    c.x = min(1.0, max(0.0, hx + ox))
                    c.y = min(1.0, max(0.0, hy + oy))
                    self._pair_ref.pop(c.id, None)
                elif len(c.held_by) == 2:
                    self._two_handed(c, hands)

    def _two_handed(self, card: Card, hands) -> None:
        """Scale and rotate from the span and angle between two hands."""
        ax, ay, _ = hands[card.held_by[0]]
        bx, by, _ = hands[card.held_by[1]]
        span = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        angle = math.atan2(by - ay, bx - ax)

        ref = self._pair_ref.get(card.id)
        if ref is None:
            # First frame of a two-handed grab: remember where it started.
            # Without a reference the object jumps to whatever scale the
            # current span happens to imply, which looks like a glitch.
            if span > 1e-6:
                self._pair_ref[card.id] = (span, angle, card.scale, card.rot)
            return

        ref_span, ref_angle, ref_scale, ref_rot = ref
        if ref_span <= 1e-6 or span <= 1e-6:
            return
        card.scale = min(MAX_SCALE, max(MIN_SCALE, ref_scale * (span / ref_span)))
        card.rot = ref_rot + (angle - ref_angle)
        card.x = min(1.0, max(0.0, (ax + bx) / 2))
        card.y = min(1.0, max(0.0, (ay + by) / 2))

    def _nearest(self, hx: float, hy: float, hand: int) -> Optional[Card]:
        """The closest grabbable object within reach, or None.

        Searched newest-first so something just put up wins over one buried
        behind it — what is on top is what you are reaching for.
        """
        best, best_d = None, GRAB_RADIUS
        for c in reversed(self._cards):
            if len(c.held_by) >= 2 or hand in c.held_by:
                continue
            d = ((c.x - hx) ** 2 + (c.y - hy) ** 2) ** 0.5
            if d < best_d:
                best, best_d = c, d
        return best


# One board per process — the dashboard and the tracker must be looking at the
# same one, and passing it through every layer would be worse.
_board: Optional[Board] = None
_board_lock = threading.Lock()


def get_board() -> Board:
    global _board
    with _board_lock:
        if _board is None:
            _board = Board()
        return _board
