"""Apex's own glass board — cards you move with your hands.

`barehands` does this too, and better in places: it has three.js physics, 3D
models and exploded views that this deliberately does not attempt. What this has
instead is a different architecture, and the difference is not cosmetic.

## Why Apex's board is not a copy of theirs

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
browser cannot open it for a video background — the frames have to travel the
other way, which is what `handtrack.latest_frame()` was already for.

## What goes on it

Apex's own data: what it just said, what it remembers, what it is watching. A
folder of markdown is barehands' answer to "what should be on a board"; Apex has
a better one, because it has a brain to show.

Everything here is pure state — no sockets, no rendering, no camera. The board
is a list of cards and the rules for moving them, and that is testable without a
browser, which is the half that would otherwise never be exercised.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

# Cards live in window fractions, exactly like the cursor stream feeding them,
# so nothing has to know the display size and a resized window moves nothing.
CARD_W = 0.22
CARD_H = 0.16

# A pinch grabs the nearest card within this radius. Generous, because the
# alternative failure — reaching for a card and getting nothing — reads as the
# tracking being broken rather than as a miss.
GRAB_RADIUS = 0.14

MAX_CARDS = 24


class Card:
    """One thing on the glass."""

    __slots__ = ("id", "kind", "title", "body", "x", "y", "scale",
                 "held_by", "created")

    def __init__(self, kind: str, title: str, body: str = "",
                 x: float = 0.5, y: float = 0.5):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind          # card | event | memory | answer
        self.title = title
        self.body = body
        self.x, self.y = x, y
        self.scale = 1.0
        self.held_by: Optional[int] = None
        self.created = time.time()

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "body": self.body, "x": round(self.x, 4), "y": round(self.y, 4),
                "scale": round(self.scale, 3), "held": self.held_by is not None}


class Board:
    """The cards, and what hands do to them.

    Thread-safe because the tracker thread drives it while the dashboard thread
    reads it — the same split every watcher in this codebase has.
    """

    def __init__(self, max_cards: int = MAX_CARDS):
        self._cards: list[Card] = []
        self._lock = threading.Lock()
        self._max = max_cards
        # Where each hand was last seen holding something, so a drag moves the
        # card by the hand's DELTA rather than snapping its centre to the
        # fingertip. Snapping looks like the card jumping into your hand.
        self._grab_offset: dict[int, tuple[float, float]] = {}

    # -- content ----------------------------------------------------------
    def add(self, kind: str, title: str, body: str = "",
            x: float = 0.5, y: float = 0.35) -> Card:
        card = Card(kind, title, body, x, y)
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
        return n

    def remove(self, card_id: str) -> bool:
        with self._lock:
            for i, c in enumerate(self._cards):
                if c.id == card_id:
                    self._cards.pop(i)
                    return True
        return False

    def cards(self) -> list[dict]:
        with self._lock:
            return [c.as_dict() for c in self._cards]

    def count(self) -> int:
        with self._lock:
            return len(self._cards)

    # -- hands ------------------------------------------------------------
    def apply_hands(self, cursors) -> None:
        """Move cards according to this frame's hands.

        `cursors` is the same `(x, y, pinched)` shape the recognizer takes, so
        the board and the gesture engine read one stream rather than two that
        could disagree about where your hand is.
        """
        if not cursors:
            # Hands gone: release everything. Without this a card stays stuck to
            # a hand that left the frame, and the only way to free it is to
            # reach back to exactly where it was.
            with self._lock:
                for c in self._cards:
                    c.held_by = None
                self._grab_offset.clear()
            return

        with self._lock:
            for idx, cur in enumerate(cursors):
                try:
                    hx, hy, pinched = float(cur[0]), float(cur[1]), bool(cur[2])
                except (TypeError, ValueError, IndexError, KeyError):
                    # KeyError is in there because a dict subscripts by key, not
                    # position — `{}[0]` raises KeyError, not IndexError, and
                    # this runs on the tracker thread where an uncaught raise
                    # takes hand tracking down along with the board.
                    continue

                held = next((c for c in self._cards if c.held_by == idx), None)

                if not pinched:
                    if held is not None:
                        held.held_by = None
                        self._grab_offset.pop(idx, None)
                    continue

                if held is None:
                    target = self._nearest(hx, hy, idx)
                    if target is None:
                        continue
                    target.held_by = idx
                    # Keep where in the card you grabbed it, so it does not jump.
                    self._grab_offset[idx] = (target.x - hx, target.y - hy)
                    held = target

                ox, oy = self._grab_offset.get(idx, (0.0, 0.0))
                held.x = min(1.0, max(0.0, hx + ox))
                held.y = min(1.0, max(0.0, hy + oy))

    def _nearest(self, hx: float, hy: float, hand: int) -> Optional[Card]:
        """The closest free card within reach, or None.

        Searched newest-first so a card just put up wins over one buried behind
        it — what is on top is what you are reaching for.
        """
        best, best_d = None, GRAB_RADIUS
        for c in reversed(self._cards):
            if c.held_by is not None and c.held_by != hand:
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
