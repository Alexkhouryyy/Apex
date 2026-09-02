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
        for cur in (cursors or []):
            try:
                open_palm = bool(cur[3]) if len(cur) > 3 else False
                out.append((float(cur[0]), float(cur[1]), bool(cur[2]), open_palm))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        return out

    def hand_state(self, idx: int) -> str:
        """What hand slot `idx` is doing right now — see HandState."""
        with self._lock:
            return self._hand_state.get(idx, HandState.IDLE)

    def apply_hands(self, cursors, now: Optional[float] = None) -> None:
        """Move, scale and rotate according to this frame's hands.

        `cursors` is the same `(x, y, pinched, open_palm)` shape the recognizer
        reads the first three of, so the board and the gesture engine read one
        stream rather than two that could disagree about where your hand is.

        `now` defaults to wall-clock time; a caller may pass it explicitly (as
        tests do) so a recorded frame sequence replays deterministically rather
        than racing the dwell timer against real elapsed time.
        """
        now = now if now is not None else time.time()
        hands = self.read_cursors(cursors)
        if not hands:
            # Hands gone: release everything. Without this an object stays stuck
            # to a hand that left the frame, and the only way to free it is to
            # reach back to exactly where it was. Not a cancel — the position
            # it was left at is kept, same as an ordinary release.
            with self._lock:
                released = [(c, self._pre_grab.get(c.id))
                            for c in self._cards if c.held_by]
                for c in self._cards:
                    c.held_by = []
                self._grab_offset.clear()
                self._pair_ref.clear()
                self._armed_since.clear()
                self._pre_grab.clear()
                self._hand_state.clear()
            # Hands leaving the frame ends a hold as surely as un-pinching does,
            # so it commits the same way — otherwise walking away from the
            # camera mid-drag would silently discard the move.
            for c, pre in released:
                self._write(c)
                after = (c.x, c.y, c.scale, c.rot)
                if pre is not None and tuple(pre) != after:
                    self._record({"kind": "transform", "id": c.id,
                                  "title": c.title, "before": tuple(pre),
                                  "after": after})
            return

        with self._lock:
            # Drop holds whose hand let go, vanished, or opened palm. Open palm
            # on EITHER holder cancels the whole hold — restoring the pre-grab
            # snapshot and dropping every hand on it, not just the one that
            # opened — because "always available as escape" means the escape
            # has to work regardless of which hand a two-handed grab's other
            # participant is doing.
            released = []
            for c in self._cards:
                cancelled = any(
                    i < len(hands) and hands[i][3] for i in c.held_by)
                if cancelled:
                    pre = self._pre_grab.pop(c.id, None)
                    if pre is not None:
                        c.x, c.y, c.scale, c.rot = pre
                    kept: list = []
                else:
                    kept = [i for i in c.held_by if i < len(hands) and hands[i][2]]
                if len(kept) != len(c.held_by):
                    # The pair changed, so the two-handed reference is stale.
                    self._pair_ref.pop(c.id, None)
                if not kept:
                    pre = self._pre_grab.pop(c.id, None)
                    if c.held_by:
                        # Held a moment ago, held by nothing now: this is the
                        # commit point. A cancel lands here too — it reverted
                        # the card, and that revert is just as much the state
                        # worth keeping as a deliberate drop would be.
                        released.append((c, pre))
                c.held_by = kept

            for idx, (hx, hy, pinched, open_palm) in enumerate(hands):
                if open_palm or not pinched:
                    self._grab_offset.pop(idx, None)
                    self._armed_since.pop(idx, None)
                    self._hand_state[idx] = HandState.IDLE
                    continue
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
                    self._grab_offset[idx] = (target.x - hx, target.y - hy)
                    if was_unheld:
                        self._pre_grab[target.id] = (
                            target.x, target.y, target.scale, target.rot)
                        self._hand_state[idx] = HandState.GRABBED
                    else:
                        self._pair_ref.pop(target.id, None)
                        self._hand_state[idx] = HandState.TRANSFORMING

            for c in self._cards:
                if len(c.held_by) == 1:
                    idx = c.held_by[0]
                    hx, hy = hands[idx][0], hands[idx][1]
                    ox, oy = self._grab_offset.get(idx, (0.0, 0.0))
                    c.x = min(1.0, max(0.0, hx + ox))
                    c.y = min(1.0, max(0.0, hy + oy))
                    self._pair_ref.pop(c.id, None)
                elif len(c.held_by) == 2:
                    self._two_handed(c, hands)

        # Outside the lock, and only for cards a hand just let go of — the
        # whole point of committing on release rather than per frame.
        for c, pre in released:
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
