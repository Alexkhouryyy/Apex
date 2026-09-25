"""Apex's own glass board — cards and 3D models you move with your hands.

`barehands` does this too. What this has instead of a copy of their design is a
different architecture, and the difference is not cosmetic.

## Why Apex's board is not a barehands clone

barehands runs MediaPipe **in the browser**, because it has no backend to run it
in. That forces three things on it: a ~26 MB WASM download on first load, the
page competing with itself for the GPU, and — the one that actually hurts —
tracking that stops dead the moment the tab is not in front, because browsers
pause `requestAnimationFrame` — the reason Apex no longer integrates with it at
all; in practice it never reliably tracked hands either.

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

## Two safety rules, not just an interaction

A pinch does not grab on its own first frame — it must hold for
`ARM_DWELL_SECONDS` before it commits. Without this, one falsely-detected
pinch frame (tracking is probabilistic; it happens) grabs whatever object is
nearest, which reads as the board randomly stealing things rather than as a
tracking hiccup.

**An open palm always cancels**, on whichever hand is holding, and restores
the object to exactly where it was before that hold began — not wherever the
drag currently sits, which is what an ordinary release does. That difference
is the point: a grab you did not mean to make, or a transform that went
somewhere you did not intend, has one unambiguous way out that works
regardless of what state the interaction is in.

## Persistence, and when it is allowed to write

The board survives a restart. It did not until now, and that was the single
largest gap between this codebase and its own design document, whose rule is:
*"create → display → manipulate → voice-edit → persist → restart → restore"* —
a loop that failed at the fifth step because the board was an in-memory
singleton and every card died with the process.

**Transient gesture motion is deliberately NOT written continuously.** Hands
move the board at ~15 Hz; persisting every frame would be 15 writes a second
of positions nobody asked to keep, and would record the middle of a drag as
though it were a decision. The rule (the design doc's own) is *commit on
release*: content changes (add, remove, clear, re-src) write immediately
because they are explicit acts, and a card's position/scale/rotation is
written when the last hand lets go of it.

That has a consequence worth stating plainly rather than discovering later: if
Apex is killed mid-drag, that card reverts to where it was before the drag
began. That is the correct trade — the alternative is treating an interrupted
gesture as an intention.

Everything else here is pure state — no sockets, no rendering, no camera. The
board is a list of objects and the rules for moving them, and that stays
testable without a browser, which is the half that would otherwise never be
exercised.
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

# How long a pinch must hold before it commits to a grab. The gesture safety
# contract this implements ("confidence, dwell time, hysteresis, and cooldown
# prevent flicker and accidental repeated activation") applied as an actual
# behaviour: a single false-positive frame from the tracker used to grab
# whatever was nearest immediately. Short enough that a real, deliberate pinch
# never feels delayed — at BOARD_FPS's 15 Hz this is under two frames.
ARM_DWELL_SECONDS = 0.12

# How many operations "undo" can walk back through. Bounded because each entry
# holds card snapshots, and a board left running for weeks would otherwise grow
# one slowly forever.
UNDO_DEPTH = 50

# --- Jarvis-style interaction ------------------------------------------------
#
# How long "what you pointed at" is remembered. You point, THEN you speak, and
# a spoken sentence plus the tap to send it takes several seconds — a memory
# shorter than that would forget the object before the words that name it
# arrive. Long enough to survive speech, short enough that "this" does not
# quietly mean something you pointed at a minute ago; Apex is told the age so
# it can ask when it is stale.
POINT_MEMORY_SECONDS = 8.0
# An open hand has to stay over a card this long before it counts as pointing
# at it. Without it, a hand crossing a card on its way out of view replaced
# the card you had deliberately pointed at.
POINT_DWELL_SECONDS = 0.3

# A flick throws a card off the board. Measured on the HAND, not the card: the
# card is clamped to the board, so a card flung at the edge stops dead there
# and would report no speed at all.
#   FLICK_SPEED           window-widths per second the hand must be moving at
#                         the moment of release. An ordinary set-down is slow.
#   FLICK_WINDOW_SECONDS  how far back the speed is measured over.
#   FLICK_PROJECT_SECONDS the throw has to be heading OFF the board: where the
#                         card would be this long after release must be
#                         outside it. A fast move across the middle is a
#                         drag that ended quickly, not a throw.
#
# These have measured nobody yet — first guesses, set on the safe side. A throw
# DELETES a card, so the asymmetry runs one way: a missed throw costs a second
# flick, a false one loses something. The first values (1.2, 0.3 s) treated a
# brisk drag ending in the middle of the board as a throw — a card moving at
# two widths a second, released at 0.5, projected past the edge. Tune these
# from a real hand the way HANDTRACK_PINCH_RATIO was.
FLICK_SPEED = 1.5
FLICK_WINDOW_SECONDS = 0.15
FLICK_PROJECT_SECONDS = 0.2
TRAIL_SECONDS = 0.4
MIN_FLICK_SPAN_SECONDS = 0.03

# A TAP is a pinch on an object let go of quickly without moving it: "tell me
# about this". Told apart from the other things a pinch does by what it is
# NOT — a grab moves the object (more than TAP_MOVE), a hold lasts (more than
# TAP_SECONDS from pick-up), a throw is fast, and two hands are a transform.
# Timed from the moment the grab commits, i.e. after ARM_DWELL_SECONDS, so a
# whole tap is a pinch of roughly 0.15-0.45 s. First guesses, like the flick
# values: a missed tap costs a second tap; a false one costs an answer you
# did not ask for, so both are on the strict side.
TAP_SECONDS = 0.35
TAP_MOVE = 0.03

# Where a card summoned by voice appears, if a hand was seen this recently:
# at the hand. Older than this and the hand is probably down — the default
# spot is better than a stale one.
HAND_ANCHOR_SECONDS = 6.0

# Swipes are ignored this long after a release, because the flick that threw
# a card IS a fast directional movement and the recognizer would read the tail
# of it as a swipe.
SWIPE_QUIET_AFTER_RELEASE = 0.8
# A hand MediaPipe misses for a frame or two (motion blur mid-drag) keeps its
# hold for this long; only after it does the card count as let go. One missed
# frame used to drop the card, and on a brisk drag throw it away. Shared with
# agent/handtrack.py, which keeps the hand's identity and pinch that long too.
HAND_LOSS_GRACE_SECONDS = 0.25
# Frames further apart than this mean the camera stalled, not that the hand
# moved. Motion recorded before a stall is not evidence of a throw after it.
STALL_SECONDS = 0.5

EVENT_BACKLOG = 50


def flick_velocity(trail, window: float = FLICK_WINDOW_SECONDS):
    """(vx, vy) in window-fractions per second over the last `window` of the
    trail, or None if there is not enough of it to say.

    Pure, so the whole throw decision can be tested with a table of samples.
    """
    if not trail or len(trail) < 2:
        return None
    t_end = trail[-1][0]
    recent = [s for s in trail if s[0] >= t_end - window]
    if len(recent) < 2:
        recent = list(trail[-2:])
    (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
    dt = t1 - t0
    # Speed over less than ~one camera frame is not a measurement. Frames that
    # arrive microseconds apart — repeated timestamps, or any caller not
    # passing real time — turn an ordinary 0.3 move into thousands of widths a
    # second, and the first version of this threw away a card that had just
    # been set down in the middle of a drag test. 30 ms is under one frame at
    # the tracker's 20 Hz, so two real consecutive frames always qualify.
    if dt < MIN_FLICK_SPAN_SECONDS:
        return None
    return ((x1 - x0) / dt, (y1 - y0) / dt)


def is_flick(trail) -> bool:
    """Fast enough AND heading off the board. Both, deliberately.

    Speed alone would throw away a card you set down briskly in the middle of
    the board. Direction alone would throw away one you slid slowly to the
    edge. A throw is a fast movement that would carry the card off the board.
    """
    v = flick_velocity(trail)
    if v is None:
        return False
    vx, vy = v
    if (vx * vx + vy * vy) ** 0.5 < FLICK_SPEED:
        return False
    _, x, y = trail[-1]
    px, py = x + vx * FLICK_PROJECT_SECONDS, y + vy * FLICK_PROJECT_SECONDS
    return not (0.0 <= px <= 1.0 and 0.0 <= py <= 1.0)


class HandState:
    """Named states for one tracked hand slot, for introspection and tests.

    Not used to DRIVE behaviour — the logic below reads `held_by` and the
    dwell timers directly — but exposing the state a reader would otherwise
    have to reconstruct from those is the whole value of naming it.
    """
    IDLE = "idle"
    ARMED = "armed"          # pinched, dwell timer running, not yet committed
    GRABBED = "grabbed"      # one hand, holding
    TRANSFORMING = "transforming"   # two hands, scaling/rotating


def init_db() -> None:
    """Create the board_cards table. Idempotent, like every other module's.

    Called from main.py's boot sequence alongside the other 27 — see
    agent/scheduler.py's init_db for what happens to the one module that does
    not follow this convention.
    """
    from agent import longterm
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS board_cards (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                src TEXT NOT NULL DEFAULT '',
                x REAL NOT NULL,
                y REAL NOT NULL,
                scale REAL NOT NULL DEFAULT 1.0,
                rot REAL NOT NULL DEFAULT 0.0,
                created REAL NOT NULL
            )
        """)


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
        # When each pinched-but-not-yet-committed hand first became pinched.
        # ARM_DWELL_SECONDS later, it commits to a grab — the debounce that
        # keeps a one-frame tracking flicker from grabbing whatever is nearest.
        self._armed_since: dict[int, float] = {}
        # A card's x/y/scale/rot at the moment it was first grabbed (by
        # whichever hand grabbed it first — a second hand joining does not
        # reset this). An open-palm cancel restores exactly these values,
        # which is the difference between "cancel" and an ordinary release:
        # release keeps wherever the drag currently is, cancel undoes it.
        self._pre_grab: dict[str, tuple] = {}
        # Reported by hand_state() for tests and any future UI — see HandState.
        self._hand_state: dict[int, str] = {}
        # The last frame's hands, so hand_report() can explain a
        # near-miss without the caller having to supply them again.
        self._last_hands: list = []
        # Off for a bare Board() so tests get pure in-memory behaviour without
        # touching the real database; get_board() turns it on for the live one.
        self.persist = False
        # Reversible history. The design doc's History command family is
        # "undo, redo, compare versions, name, save, restore", and the golden
        # demonstration says the words "Undo that" out loud — there was no
        # undo at all. Capped because an unbounded stack of card snapshots is
        # a slow memory leak on a board that runs for weeks.
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._selected: str | None = None
        # What an open hand was last pointing at, and when. See
        # POINT_MEMORY_SECONDS.
        self._pointed: tuple[str, float] | None = None
        # Per hand: the card it is hovering over and since when, for
        # POINT_DWELL_SECONDS.
        self._point_candidate: dict = {}
        # Per hand id: when it was last in a frame, for HAND_LOSS_GRACE_SECONDS.
        self._hand_last_seen: dict = {}
        self._last_frame_at: float | None = None
        # Recent hand positions of a card held by ONE hand, for the flick.
        self._trail: dict[str, list] = {}
        # card id -> [picked-up-at, hand x, hand y, furthest the hand moved]
        # while one hand holds it: what decides, on release, whether it was a tap.
        self._tap_probe: dict[str, list] = {}
        # The last place a hand was seen, for summoning a card to it.
        self._hand_seen: tuple[float, float, float] | None = None
        self._last_release_at: float = 0.0
        # Things the browser should react to that are not state — a card
        # thrown away, Apex summoned. Numbered so each socket can ask for only
        # what it has not seen.
        self._events: list[dict] = []
        self._event_seq = 0

    # -- history -----------------------------------------------------------
    @staticmethod
    def _snapshot(card: "Card") -> dict:
        """Everything needed to rebuild a card exactly, id included — a
        restored card with a new id would break every reference to it."""
        return {"id": card.id, "kind": card.kind, "title": card.title,
                "body": card.body, "src": card.src, "x": card.x, "y": card.y,
                "scale": card.scale, "rot": card.rot, "created": card.created}

    @staticmethod
    def _from_snapshot(snap: dict) -> "Card":
        c = Card(snap["kind"], snap["title"], snap["body"],
                 snap["x"], snap["y"], snap["src"])
        c.id, c.scale, c.rot, c.created = (
            snap["id"], snap["scale"], snap["rot"], snap["created"])
        return c

    def _record(self, op: dict) -> None:
        """Push a reversible operation. A new action discards the redo branch,
        which is what every undo stack does and what a user expects: once you
        change course, the future you abandoned is gone."""
        self._undo.append(op)
        del self._undo[:-UNDO_DEPTH]
        self._redo.clear()

    def _apply(self, op: dict, *, forward: bool) -> str:
        """Run an operation in either direction. One function for both so undo
        and redo cannot drift apart — the commonest way a redo quietly stops
        being the exact inverse of its undo."""
        kind = op["kind"]
        with self._lock:
            if kind == "add":
                snap = op["card"]
                if forward:
                    self._cards.append(self._from_snapshot(snap))
                else:
                    self._cards = [c for c in self._cards if c.id != snap["id"]]
                what = f"added '{snap['title']}'" if forward else f"removed '{snap['title']}'"
            elif kind == "remove":
                snap = op["card"]
                if forward:
                    self._cards = [c for c in self._cards if c.id != snap["id"]]
                else:
                    self._cards.append(self._from_snapshot(snap))
                what = f"removed '{snap['title']}'" if forward else f"restored '{snap['title']}'"
            elif kind == "clear":
                snaps = op["cards"]
                if forward:
                    self._cards = []
                else:
                    self._cards = [self._from_snapshot(s) for s in snaps]
                what = (f"cleared {len(snaps)} card(s)" if forward
                        else f"put {len(snaps)} card(s) back")
            elif kind == "transform":
                target = next((c for c in self._cards if c.id == op["id"]), None)
                state = op["after"] if forward else op["before"]
                if target is not None:
                    target.x, target.y, target.scale, target.rot = state
                what = f"moved '{op.get('title', '')}'"
            elif kind == "src":
                target = next((c for c in self._cards if c.id == op["id"]), None)
                if target is not None:
                    target.src = op["after"] if forward else op["before"]
                what = f"changed what '{op.get('title', '')}' shows"
            else:
                what = "did nothing"
            # Snapshot what needs writing while still holding the lock; the
            # writes themselves happen outside it.
            live = {c.id: c for c in self._cards}
        # Persist the result either way. An undo that survives in memory but
        # not on disk would come back undone-then-redone after a restart.
        if kind in ("add", "remove", "clear"):
            self._persist_all()
        else:
            target = live.get(op.get("id"))
            if target is not None:
                self._write(target)
        return what

    def _persist_all(self) -> None:
        """Rewrite storage to match memory exactly — used after operations that
        add or delete cards, where a per-card write cannot express a removal."""
        if not self.persist:
            return
        try:
            from agent import longterm
            with self._lock:
                snaps = [self._snapshot(c) for c in self._cards]
            with longterm._conn() as c:
                c.execute("DELETE FROM board_cards")
                for s in snaps:
                    c.execute(
                        """INSERT INTO board_cards
                           (id, kind, title, body, src, x, y, scale, rot, created)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (s["id"], s["kind"], s["title"], s["body"], s["src"],
                         s["x"], s["y"], s["scale"], s["rot"], s["created"]))
        except Exception as e:
            print(f"[Board] could not save the board: {e}")

    def undo(self) -> Optional[str]:
        """Reverse the last operation. None when there is nothing to undo."""
        if not self._undo:
            return None
        op = self._undo.pop()
        what = self._apply(op, forward=False)
        self._redo.append(op)
        return what

    def redo(self) -> Optional[str]:
        """Re-apply the last undone operation. None when there is none."""
        if not self._redo:
            return None
        op = self._redo.pop()
        what = self._apply(op, forward=True)
        self._undo.append(op)
        return what

    # -- persistence -------------------------------------------------------
    def _write(self, card: "Card") -> None:
        """Upsert one card. Never raises: the board is a view, and losing its
        durability must not take down the tracker thread that drives it."""
        if not self.persist:
            return
        try:
            from agent import longterm
            with longterm._conn() as c:
                c.execute(
                    """INSERT INTO board_cards
                       (id, kind, title, body, src, x, y, scale, rot, created)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         kind=excluded.kind, title=excluded.title,
                         body=excluded.body, src=excluded.src, x=excluded.x,
                         y=excluded.y, scale=excluded.scale, rot=excluded.rot""",
                    (card.id, card.kind, card.title, card.body, card.src,
                     card.x, card.y, card.scale, card.rot, card.created))
        except Exception as e:
            print(f"[Board] could not save '{card.title}': {e}")

    def _forget(self, card_ids) -> None:
        if not self.persist:
            return
        try:
            from agent import longterm
            with longterm._conn() as c:
                for cid in card_ids:
                    c.execute("DELETE FROM board_cards WHERE id = ?", (cid,))
        except Exception as e:
            print(f"[Board] could not remove card(s) from storage: {e}")

    def restore(self) -> int:
        """Load saved cards back onto the board. Returns how many came back.

        Held state is deliberately not restored — no hand is holding anything
        at boot, and a card that came back already "held" by a hand index from
        a previous session could never be released.
        """
        try:
            from agent import longterm
            with longterm._conn() as c:
                rows = c.execute(
                    """SELECT id, kind, title, body, src, x, y, scale, rot, created
                       FROM board_cards ORDER BY created ASC""").fetchall()
        except Exception as e:
            print(f"[Board] could not restore saved cards: {e}")
            return 0
        with self._lock:
            self._cards = []
            for r in rows:
                card = Card(r[1], r[2], r[3], r[5], r[6], r[4])
                card.id, card.scale, card.rot, card.created = r[0], r[7], r[8], r[9]
                self._cards.append(card)
            while len(self._cards) > self._max:
                self._cards.pop(0)
            return len(self._cards)

    # -- content ----------------------------------------------------------
    def add(self, kind: str, title: str, body: str = "",
            x: float = 0.5, y: float = 0.35, src: str = "") -> Card:
        card = Card(kind, title, body, x, y, src)
        with self._lock:
            self._cards.append(card)
            # Oldest out first. A board that grows without limit becomes
            # unusable long before it becomes slow.
            evicted = []
            while len(self._cards) > self._max:
                evicted.append(self._cards.pop(0).id)
        # Outside the lock: _write and _forget open their own connection, and
        # holding the board's lock across a database call would let a slow
        # disk stall the tracker thread mid-frame.
        self._forget(evicted)
        self._write(card)
        self._record({"kind": "add", "card": self._snapshot(card)})
        return card

    def clear(self) -> int:
        with self._lock:
            n = len(self._cards)
            ids = [c.id for c in self._cards]
            snaps = [self._snapshot(c) for c in self._cards]
            self._cards.clear()
            self._grab_offset.clear()
            self._pair_ref.clear()
            self._armed_since.clear()
            self._pre_grab.clear()
            self._tap_probe.clear()
            self._hand_state.clear()
        self._forget(ids)
        if snaps:
            self._record({"kind": "clear", "cards": snaps})
        return n

    def set_src(self, card_id: str, src: str) -> bool:
        """Point an existing card at a different prop file — used when a
        recolor produces a new export that must replace what the card shows,
        without disturbing its position, scale or rotation."""
        found, previous = None, None
        with self._lock:
            for c in self._cards:
                if c.id == card_id:
                    previous = c.src
                    c.src = src
                    found = c
                    break
        if found is None:
            return False
        self._write(found)
        self._record({"kind": "src", "id": card_id, "title": found.title,
                      "before": previous, "after": src})
        return True

    def remove(self, card_id: str) -> bool:
        removed = None
        with self._lock:
            for i, c in enumerate(self._cards):
                if c.id == card_id:
                    removed = self._snapshot(self._cards.pop(i))
                    self._pair_ref.pop(card_id, None)
                    break
        if removed is None:
            return False
        self._forget([card_id])
        self._record({"kind": "remove", "card": removed})
        return True

    def cards(self) -> list[dict]:
        with self._lock:
            return [c.as_dict() for c in self._cards]

    def count(self) -> int:
        with self._lock:
            return len(self._cards)

    def selection(self) -> dict | None:
        """Keep the last grabbed object selected after the hand releases it."""
        with self._lock:
            return next((c.as_dict() for c in self._cards if c.id == self._selected), None)

    def select(self, card_id: str | None) -> dict | None:
        with self._lock:
            card = next((c for c in self._cards if c.id == card_id), None)
            if card_id is not None and card is None:
                raise ValueError("That object is no longer on the board.")
            self._selected = card_id
            return card.as_dict() if card else None

    def transform(self, card_id: str, **changes) -> dict:
        """Undoable view transform. Does not change manufacturing dimensions."""
        limits = {"x": (0., 1.), "y": (0., 1.), "scale": (.25, 4.),
                  "rot": (-100., 100.)}
        import math
        if not changes or any(k not in limits for k in changes):
            raise ValueError("Supply x, y, scale or rot.")
        for key, value in changes.items():
            if (type(value) not in (float, int) or not math.isfinite(value)
                    or not limits[key][0] <= value <= limits[key][1]):
                raise ValueError(f"{key} must be between {limits[key][0]} and {limits[key][1]}.")
        with self._lock:
            card = next((c for c in self._cards if c.id == card_id), None)
            if card is None:
                raise ValueError("That object is no longer on the board.")
            if card.held_by:
                raise ValueError("Release the object before changing its view by voice or touch.")
            before = (card.x, card.y, card.scale, card.rot)
            for key, value in changes.items():
                setattr(card, key, float(value))
            after = (card.x, card.y, card.scale, card.rot)
            self._selected = card.id
            self._record({"kind": "transform", "id": card.id, "title": card.title,
                          "before": before, "after": after})
            result = card.as_dict()
        self._write(card)
        return result

    # -- Jarvis-style interaction ----------------------------------------
    def emit(self, kind: str, **data) -> dict:
        """Something the page should react to that is not board state."""
        with self._lock:
            self._event_seq += 1
            ev = {"seq": self._event_seq, "type": kind, **data}
            self._events.append(ev)
            del self._events[:-EVENT_BACKLOG]
            return ev

    def events_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._events if e["seq"] > seq]

    def latest_event_seq(self) -> int:
        with self._lock:
            return self._event_seq

    def pointed(self, now: Optional[float] = None,
                max_age: float = POINT_MEMORY_SECONDS) -> dict | None:
        """The card an open hand last pointed at, with how long ago.

        None when nothing was pointed at recently, or the card has since gone.
        The age travels with it so the model can tell "this, right now" from
        "the thing you pointed at seven seconds ago" and ask when unsure.
        """
        now = now if now is not None else time.time()
        with self._lock:
            if self._pointed is None:
                return None
            card_id, at = self._pointed
            if now - at > max_age:
                return None
            card = next((c for c in self._cards if c.id == card_id), None)
            if card is None:
                return None
            out = card.as_dict()
        out["seconds_ago"] = round(now - at, 1)
        return out

    def hand_anchor(self, now: Optional[float] = None,
                    max_age: float = HAND_ANCHOR_SECONDS):
        """Where a summoned card should appear: at the hand, if one was up
        recently. Clamped so the whole card is on the board, not half off it."""
        now = now if now is not None else time.time()
        with self._lock:
            seen = self._hand_seen
        if seen is None or now - seen[2] > max_age:
            return None
        x = min(1.0 - CARD_W / 2, max(CARD_W / 2, seen[0]))
        y = min(1.0 - CARD_H / 2, max(CARD_H / 2, seen[1]))
        return (x, y)

    def hands_idle(self, now: Optional[float] = None) -> tuple[bool, str]:
        """False while a card is held or was just let go of — the hands are
        working the board, so a hold or a stroke is not a command to Apex."""
        now = now if now is not None else time.time()
        with self._lock:
            # A card stays held through HAND_LOSS_GRACE_SECONDS while its hand
            # is missing, so this is not implied by any hand being pinched.
            if any(c.held_by for c in self._cards):
                return False, "a card is held"
            if now - self._last_release_at < SWIPE_QUIET_AFTER_RELEASE:
                return False, "a card was just released"
        return True, ""

    def swipes_allowed(self, now: Optional[float] = None) -> tuple[bool, str]:
        """Whether a swipe should act, and if not, why.

        Moving a held card fast IS a swipe to the recognizer, which knows
        nothing about cards. Without this, dragging a card left would also page
        the selection left, and every flick would fire a swipe on its way out.
        """
        with self._lock:
            if any(len(h) > 2 and h[2] for h in self._last_hands):
                return False, "a hand is pinched (holding, or about to grab)"
        return self.hands_idle(now)

    def select_step(self, step: int) -> dict | None:
        """Move the selection to the next (+1) or previous (-1) card, wrapping.
        With nothing selected, starts from the most recent card."""
        with self._lock:
            if not self._cards:
                return None
            ids = [c.id for c in self._cards]
            if self._selected in ids:
                i = (ids.index(self._selected) + step) % len(ids)
            else:
                i = len(ids) - 1
            self._selected = ids[i]
            return self._cards[i].as_dict()

    def _throw(self, card: "Card", pre) -> None:
        """Remove a flicked card, undoably, remembering where it CAME from.

        `remove()` snapshots the card as it is, which for a throw is jammed
        against the edge it was flung at. Putting it back to its pre-grab spot
        first means "undo" returns it home rather than to the edge.
        """
        if pre is not None:
            card.x, card.y, card.scale, card.rot = pre
        with self._lock:
            if self._selected == card.id:
                self._selected = None
        if self.remove(card.id):
            self.emit("thrown", id=card.id, title=card.title)

    # -- hands ------------------------------------------------------------
    @staticmethod
    def read_cursors(cursors) -> list:
        """Normalize the cursor list, dropping anything unusable.

        Malformed entries are skipped rather than raised on: this runs on the
        tracker thread, where an exception takes hand tracking down along with
        the board. KeyError is in the caught set because a dict subscripts by
        key, not position — `{}[0]` raises KeyError, not IndexError.

        A 4th element (`open_palm`, the board's cancel gesture) is optional —
        `agent/gestures.py` reads only the first three positions of this same
        stream and neither module needs to agree on the other's use of it, so
        a 3-tuple source still works here, just without cancel available.
        """
        out = []
        for i, cur in enumerate(cursors or []):
            try:
                open_palm = bool(cur[3]) if len(cur) > 3 else False
                # The 5th element is the hand's identity (HandIdentities in
                # agent/handtrack.py). Without one, the list position stands
                # in — fine for a caller whose hands never change order.
                hid = cur[4] if len(cur) > 4 else i
                out.append((float(cur[0]), float(cur[1]), bool(cur[2]), open_palm, hid))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return out

    def hand_state(self, hand) -> str:
        """What hand `hand` (its id) is doing right now — see HandState."""
        with self._lock:
            return self._hand_state.get(hand, HandState.IDLE)

    def hand_report(self, cursors=None) -> list[dict]:
        """Why each hand is or is not holding something, for the live readout.

        A pinch that does not grab has five different causes and they look
        identical from the outside: the hand is not pinched, the pinch has not
        held for ARM_DWELL_SECONDS yet, there is no card within GRAB_RADIUS,
        the nearest card is already held by two hands, or an open palm is
        cancelling. Telling someone "it did not grab" is useless; telling them
        "nothing within reach — nearest card is 0.31 away, reach is 0.14" is
        the whole difference between a five-minute fix and giving up.

        Read-only. It recomputes the same distance `_nearest` uses rather than
        recording what `_nearest` decided, so the reach number shown is the one
        actually applied and cannot drift from it.
        """
        hands = self.read_cursors(cursors if cursors is not None else self._last_hands)
        out = []
        with self._lock:
            for hx, hy, pinched, open_palm, idx in hands:
                holding = next((c for c in self._cards if idx in c.held_by), None)
                nearest, nearest_d, blocked = None, None, False
                for c in reversed(self._cards):
                    if idx in c.held_by:
                        continue
                    d = ((c.x - hx) ** 2 + (c.y - hy) ** 2) ** 0.5
                    if nearest_d is None or d < nearest_d:
                        nearest, nearest_d = c, d
                        blocked = len(c.held_by) >= 2
                armed = self._armed_since.get(idx)
                out.append({
                    "hand": idx,
                    "state": self._hand_state.get(idx, HandState.IDLE),
                    "pinched": bool(pinched),
                    "open_palm": bool(open_palm),
                    "holding": holding.title if holding else None,
                    "nearest": nearest.title if nearest else None,
                    "distance": round(nearest_d, 4) if nearest_d is not None else None,
                    "reach": GRAB_RADIUS,
                    "in_reach": bool(nearest_d is not None and nearest_d < GRAB_RADIUS),
                    "nearest_is_full": bool(blocked),
                    "dwell_needed": ARM_DWELL_SECONDS,
                    "arming": armed is not None,
                })
        return out

    def apply_hands(self, cursors, now: Optional[float] = None) -> None:
        """Move, scale and rotate according to this frame's hands.

        `cursors` is the same `(x, y, pinched, open_palm, hand_id)` stream the
        recognizer reads the first three of, so the board and the gesture
        engine read one stream rather than two that could disagree about where
        your hand is.

        Everything is keyed by hand id, never by list position: a second hand
        coming into view reorders the list, and a hold keyed by position moved
        to the newcomer — which threw the card away, dropped it, or snapped it
        back. A hand missing from a frame keeps its hold for
        HAND_LOSS_GRACE_SECONDS; only a hand gone longer than that lets go.

        `now` defaults to wall-clock time; a caller may pass it explicitly (as
        tests do) so a recorded frame sequence replays deterministically rather
        than racing the dwell timer against real elapsed time.
        """
        now = now if now is not None else time.time()
        hands = self.read_cursors(cursors)
        byid = {h[4]: h for h in hands}
        # Recorded AFTER normalisation, so the readout explains the hands the
        # board actually acted on rather than the raw ones it was handed.
        self._last_hands = list(hands)
        if hands:
            self._hand_seen = (hands[0][0], hands[0][1], now)

        with self._lock:
            stalled = (self._last_frame_at is not None
                       and now - self._last_frame_at > STALL_SECONDS)
            self._last_frame_at = now
            if stalled:
                # No frames for a while: the trails describe motion from
                # before the stall, and a release now is not a throw.
                self._trail.clear()
            for h in hands:
                self._hand_last_seen[h[4]] = now

            def gone(hid) -> bool:
                return (hid not in byid and now - self._hand_last_seen.get(
                    hid, float("-inf")) > HAND_LOSS_GRACE_SECONDS)

            # Drop holds whose hand let go, left for good, or opened palm.
            # Open palm on EITHER holder cancels the whole hold — restoring
            # the pre-grab snapshot and dropping every hand on it, not just the
            # one that opened — because "always available as escape" means the
            # escape has to work regardless of which hand a two-handed grab's
            # other participant is doing.
            released = []
            for c in self._cards:
                # The release frame's own hand position is part of the throw:
                # the fingers open at the END of the fling, while still moving.
                # A hand missing this frame adds nothing — no position is not
                # the same as the position of whichever hand is in the list.
                if len(c.held_by) == 1 and c.id in self._trail:
                    h = byid.get(c.held_by[0])
                    if h is not None:
                        self._trail[c.id].append((now, h[0], h[1]))
                flung = len(c.held_by) == 1 and is_flick(self._trail.get(c.id))
                cancelled = any(i in byid and byid[i][3] for i in c.held_by)
                # A hand that opens WHILE flinging is finishing a throw, not
                # asking to cancel — and it is thrown, because the release
                # below carries `flung` whatever this branch does. The cancel
                # also puts the card back where it was picked up, which is
                # exactly the spot a throw's undo should return it to.
                if cancelled:
                    pre = self._pre_grab.pop(c.id, None)
                    if pre is not None:
                        c.x, c.y, c.scale, c.rot = pre
                    kept: list = []
                else:
                    kept = [i for i in c.held_by
                            if (byid[i][2] if i in byid else not gone(i))]
                if len(kept) != len(c.held_by):
                    # The pair changed, so the two-handed reference is stale.
                    self._pair_ref.pop(c.id, None)
                if not kept:
                    pre = self._pre_grab.pop(c.id, None)
                    probe = self._tap_probe.pop(c.id, None)
                    tapped = bool(c.held_by and probe and not cancelled and not flung
                                  and now - probe[0] <= TAP_SECONDS and probe[3] <= TAP_MOVE)
                    if c.held_by:
                        # Held a moment ago, held by nothing now: this is the
                        # commit point. A cancel lands here too — it reverted
                        # the card, and that revert is just as much the state
                        # worth keeping as a deliberate drop would be. Flinging
                        # a hand out of the camera's view lands here too, once
                        # the grace runs out, and is the most natural throw
                        # there is.
                        released.append((c, pre, flung, tapped))
                        self._last_release_at = now
                    self._trail.pop(c.id, None)
                c.held_by = kept

            # Per-hand state for hands that are gone for good.
            for hid in [k for k in self._hand_last_seen if gone(k)]:
                self._hand_last_seen.pop(hid, None)
                self._grab_offset.pop(hid, None)
                self._armed_since.pop(hid, None)
                self._hand_state.pop(hid, None)
                self._point_candidate.pop(hid, None)

            pointing = []
            for hx, hy, pinched, open_palm, idx in hands:
                if open_palm or not pinched:
                    self._grab_offset.pop(idx, None)
                    self._armed_since.pop(idx, None)
                    self._hand_state[idx] = HandState.IDLE
                    # An open hand over a card is pointing at it — the same
                    # reach a pinch would grab with, so "this" means exactly
                    # the card a pinch would have picked up.
                    target = self._nearest(hx, hy, idx)
                    cand = self._point_candidate.get(idx)
                    if target is None:
                        self._point_candidate.pop(idx, None)
                    elif cand is None or cand[0] != target.id:
                        self._point_candidate[idx] = (target.id, now)
                    elif now - cand[1] >= POINT_DWELL_SECONDS:
                        pointing.append(cand)
                    continue
                self._point_candidate.pop(idx, None)
                holding = next((c for c in self._cards if idx in c.held_by), None)
                if holding is not None:
                    self._hand_state[idx] = (
                        HandState.TRANSFORMING if len(holding.held_by) == 2
                        else HandState.GRABBED)
                    continue
                # Pinched, holding nothing yet: arm, then commit once the pinch
                # has held for ARM_DWELL_SECONDS — the flicker guard.
                started = self._armed_since.get(idx)
                if started is None:
                    self._armed_since[idx] = now
                    self._hand_state[idx] = HandState.ARMED
                    continue
                if now - started < ARM_DWELL_SECONDS:
                    self._hand_state[idx] = HandState.ARMED
                    continue
                self._armed_since.pop(idx, None)
                target = self._nearest(hx, hy, idx)
                if target is None:
                    self._hand_state[idx] = HandState.IDLE
                    continue
                # A second hand may join something already held — that is how a
                # two-handed grab begins.
                if len(target.held_by) < 2:
                    was_unheld = not target.held_by
                    target.held_by.append(idx)
                    self._selected = target.id
                    self._grab_offset[idx] = (target.x - hx, target.y - hy)
                    if was_unheld:
                        self._pre_grab[target.id] = (
                            target.x, target.y, target.scale, target.rot)
                        self._trail[target.id] = [(now, hx, hy)]
                        self._tap_probe[target.id] = [now, hx, hy, 0.0]
                        self._hand_state[idx] = HandState.GRABBED
                    else:
                        self._pair_ref.pop(target.id, None)
                        self._tap_probe.pop(target.id, None)   # two hands: not a tap
                        self._hand_state[idx] = HandState.TRANSFORMING
            if pointing:
                # Two open hands over cards: the one that arrived at its card
                # most recently is the one doing the pointing — a hand resting
                # over a card has been there all along.
                card_id, _since = max(pointing, key=lambda cand: cand[1])
                self._pointed = (card_id, now)

            for c in self._cards:
                if len(c.held_by) == 1:
                    h = byid.get(c.held_by[0])
                    if h is None:
                        continue          # missed this frame: stay put
                    hx, hy = h[0], h[1]
                    trail = self._trail.setdefault(c.id, [])
                    if not trail or trail[-1][0] != now:
                        trail.append((now, hx, hy))
                    del trail[:-64]
                    cutoff = now - TRAIL_SECONDS
                    while len(trail) > 2 and trail[0][0] < cutoff:
                        trail.pop(0)
                    probe = self._tap_probe.get(c.id)
                    if probe is not None:
                        probe[3] = max(probe[3], math.hypot(hx - probe[1], hy - probe[2]))
                    ox, oy = self._grab_offset.get(c.held_by[0], (0.0, 0.0))
                    c.x = min(1.0, max(0.0, hx + ox))
                    c.y = min(1.0, max(0.0, hy + oy))
                    self._pair_ref.pop(c.id, None)
                elif len(c.held_by) == 2:
                    # A two-handed hold cannot be flicked, and that is
                    # structural: the trail is only ever APPENDED while one
                    # hand holds, so there is no speed to measure. This also
                    # stops a stale one-hand trail being resumed if the card
                    # goes back to one hand.
                    self._trail.pop(c.id, None)
                    if all(i in byid for i in c.held_by):
                        self._two_handed(c, byid)

        # Outside the lock, and only for cards a hand just let go of — the
        # whole point of committing on release rather than per frame.
        for c, pre, thrown, tapped in released:
            if thrown:
                self._throw(c, pre)
                continue
            if tapped:
                # Put back exactly — a tap must not nudge it — with nothing
                # for undo to spend a step on, and "this" now means it.
                if pre is not None:
                    c.x, c.y, c.scale, c.rot = pre
                with self._lock:
                    self._pointed = (c.id, now)
                    self._selected = c.id
                self._write(c)
                self.emit("tapped", id=c.id, title=c.title, object_kind=c.kind)
                continue
            self._write(c)
            after = (c.x, c.y, c.scale, c.rot)
            # A cancel already put the card back, so before == after and there
            # is nothing to undo. Recording it anyway would make "undo" spend
            # a step doing nothing visible, which reads as undo being broken.
            if pre is not None and tuple(pre) != after:
                self._record({"kind": "transform", "id": c.id, "title": c.title,
                              "before": tuple(pre), "after": after})

    def _two_handed(self, card: Card, hands) -> None:
        """Scale and rotate from the span and angle between two hands."""
        ax, ay = hands[card.held_by[0]][0], hands[card.held_by[0]][1]
        bx, by = hands[card.held_by[1]][0], hands[card.held_by[1]][1]
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
    """The live board — durable, and restored from the last session on first use.

    A bare `Board()` stays in-memory (see `Board.persist`), which is what tests
    want. This one is the real thing, so it saves and comes back.
    """
    global _board
    with _board_lock:
        if _board is None:
            b = Board()
            b.persist = True
            try:
                n = b.restore()
                if n:
                    print(f"[Board] Restored {n} card(s) from the last session.")
            except Exception as e:
                # A board that cannot restore is still a usable empty board.
                print(f"[Board] could not restore: {e}")
            _board = b
        return _board
