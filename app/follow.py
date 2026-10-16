"""Keep the desktop Companion bubble beside your cursor.

Follow mode is on by default (COMPANION_FOLLOW_CURSOR; the Companion "Follow"
button or Ctrl+Alt+F turns it off): the small always-on-top Companion window
rides next to the mouse pointer the way a cursor buddy does: just below and to the right of it, flipping to the
other side near a screen edge, never covering the pointer, and staying inside
the work area of whichever monitor the pointer is on (so not under the taskbar).

When Celine points at something on screen (screen_draw), the bubble flies over
to sit beside what she marked for as long as the mark lasts, then comes back to
the pointer — so your eye goes where she is talking about.

It only follows while the bubble is compact. While you type in the expanded
chat, dictate, or the window is hidden, it stays put — a window that ran away
from your typing would be worse than one that stayed in the corner.

The placement and smoothing are pure functions (tested on any OS); only the
cursor/monitor providers and the move call touch Windows.
"""
from __future__ import annotations

import os
import threading

OFFSET = 28          # px between the pointer and the bubble
POLL_SECONDS = 1 / 30
MIN_STEP = 2         # don't move the window for a one- or two-pixel twitch
SMOOTHING = 0.35     # fraction of the remaining distance covered per tick


def follow_position(cursor, size, area, offset=OFFSET):
    """Top-left (x, y) for a bubble of `size` (w, h) next to `cursor` (x, y),
    inside `area` (left, top, right, bottom). Prefers below-right of the pointer;
    flips left or up when it would leave the area; never covers the pointer."""
    cx, cy = int(cursor[0]), int(cursor[1])
    w, h = int(size[0]), int(size[1])
    left, top, right, bottom = (int(v) for v in area)
    x = cx + offset
    if x + w > right:                       # no room on the right: go left of the pointer
        x = cx - offset - w
    y = cy + offset
    if y + h > bottom:                      # no room below: go above the pointer
        y = cy - offset - h
    # Clamp into the work area (a bubble larger than the area pins to its corner).
    x = max(left, min(x, right - w))
    y = max(top, min(y, bottom - h))
    # A clamp can slide the bubble back over the pointer on a small area; if it
    # would cover it, push it to whichever side of the pointer has more room.
    if x <= cx < x + w and y <= cy < y + h:
        if cx - left >= right - cx:
            x = max(left, cx - offset - w)
        else:
            x = min(right - w, cx + offset)
    return x, y


def focus_point(frame, shapes):
    """The screen pixel (x, y) a drawing is about: the tip of the first arrow,
    else the centre of the first mark. `frame` holds the screenshot's left, top,
    width and height; shape points are normalized 0..1 across it. None if empty."""
    if not shapes:
        return None
    pick = next((s for s in shapes if s.get('type') == 'arrow'), shapes[0])
    points = pick.get('points') or []
    if not points:
        return None
    if pick.get('type') in {'arrow', 'line', 'curve'}:
        nx, ny = points[-1]                       # where it points / ends
    else:                                         # box, ellipse, polygon, label: its centre
        nx = sum(p[0] for p in points) / len(points)
        ny = sum(p[1] for p in points) / len(points)
    return (int(round(frame['left'] + nx * frame['width'])),
            int(round(frame['top'] + ny * frame['height'])))


def smooth(current, target, factor=SMOOTHING, snap=MIN_STEP):
    """One easing step from `current` toward `target`; snaps when close."""
    if current is None:
        return tuple(target)
    out = []
    for c, t in zip(current, target):
        d = t - c
        out.append(t if abs(d) <= snap else int(round(c + d * factor)))
    return tuple(out)


class Follower:
    """Moves the window toward the follow position on a background thread.

    `cursor()` -> (x, y); `area(x, y)` -> work area of the monitor at that point;
    `size()` -> (w, h) of the window; `move(x, y)` positions it. All injected, so
    tests drive it with fakes and Windows supplies the real ones."""

    def __init__(self, cursor, area, size, move, enabled=False, clock=None):
        import time
        self._cursor, self._area, self._size, self._move = cursor, area, size, move
        self._clock = clock or time.monotonic
        self.enabled = bool(enabled)
        self.paused = False
        self._at = None
        self._focus = None         # ((x, y), until): sit beside a mark instead of the pointer
        self._stop = threading.Event()
        self._thread = None

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self._at = None            # re-seat at once when turned back on

    def point_at(self, x, y, seconds) -> None:
        """Sit beside (x, y) — what Celine just marked — for `seconds`, then
        return to the pointer."""
        self._focus = ((int(x), int(y)), self._clock() + max(0.0, float(seconds)))

    def tick(self) -> bool:
        """One step. Returns True when the window was moved."""
        if not self.enabled or self.paused:
            return False
        focus = self._focus
        if focus and self._clock() >= focus[1]:
            focus = self._focus = None
        try:
            cx, cy = focus[0] if focus else self._cursor()
            target = follow_position((cx, cy), self._size(), self._area(cx, cy))
        except Exception:
            return False           # pointer or monitor briefly unavailable: try next tick
        nxt = smooth(self._at, target)
        if self._at is not None and abs(nxt[0] - self._at[0]) < MIN_STEP and abs(nxt[1] - self._at[1]) < MIN_STEP:
            nxt = tuple(target)        # the last few pixels: land exactly instead of stalling short
        if nxt == self._at:
            return False
        try:
            self._move(*nxt)
        except Exception:
            return False           # a failed move never kills the loop
        self._at = nxt
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name='CompanionFollow')
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            self.tick()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------- Windows providers

def windows_cursor():
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def windows_work_area(x, y):
    """Work area (excludes the taskbar) of the monitor containing (x, y)."""
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]
    user = ctypes.windll.user32
    monitor = user.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)   # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO(); info.cbSize = ctypes.sizeof(MONITORINFO)
    user.GetMonitorInfoW(monitor, ctypes.byref(info))
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def available() -> bool:
    return os.name == 'nt'
