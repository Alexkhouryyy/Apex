"""System tray icon for Apex Resident.

Five states (color-coded): idle, listening, thinking, speaking, muted.
Menu: Wake now | Mute 15min / 1h / until quit | Unmute | Open dashboard |
Show recent activity | Quit.

Icons are rendered on the fly via PIL — no PNG assets required, ships zero
binary blobs. Falls back gracefully when pystray / a display server isn't
available (logs a message and disables itself; the resident keeps running).
"""
from typing import Callable, Optional

# Icon colors per state — tuple of (fg circle, bg)
_COLORS = {
    "idle":      ((220, 220, 220), (40, 40, 40)),       # white on dark
    "listening": ((80, 230, 120),  (40, 40, 40)),       # green
    "thinking":  ((255, 200, 80),  (40, 40, 40)),       # amber
    "speaking":  ((100, 170, 255), (40, 40, 40)),       # blue
    "muted":     ((230, 80, 80),   (40, 40, 40)),       # red
}


def _make_icon_image(state: str, size: int = 64):
    """Render a tray icon for the given state. Returns a PIL Image."""
    from PIL import Image, ImageDraw
    fg, bg = _COLORS.get(state, _COLORS["idle"])
    img = Image.new("RGBA", (size, size), bg + (255,))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    # Outer ring
    draw.ellipse((pad, pad, size - pad, size - pad), outline=fg, width=3)
    # Inner dot pulses by state
    inner_pad = size // 3
    if state == "muted":
        # Diagonal slash for muted
        draw.line((pad + 4, pad + 4, size - pad - 4, size - pad - 4), fill=fg, width=4)
    elif state in ("listening", "speaking"):
        # Filled center
        draw.ellipse((inner_pad, inner_pad, size - inner_pad, size - inner_pad), fill=fg)
    elif state == "thinking":
        # Small triangle (motion)
        cx, cy = size // 2, size // 2
        r = size // 8
        draw.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fill=fg)
    # idle: just the ring
    return img


class Tray:
    """Wraps pystray. Caller drives state via set_state(name)."""

    def __init__(
        self,
        on_wake_now: Callable[[], None],
        on_mute: Callable[[int], None],           # minutes; -1 = until quit; 0 = unmute
        on_open_dashboard: Callable[[], None],
        on_show_recent: Callable[[], None],
        on_quit: Callable[[], None],
        on_companion: Optional[Callable[[], None]] = None,
    ):
        self._on_wake_now = on_wake_now
        self._on_mute = on_mute
        self._on_open_dashboard = on_open_dashboard
        self._on_show_recent = on_show_recent
        self._on_quit = on_quit
        self._on_companion = on_companion
        self._icon = None
        self._state = "idle"

    def start(self) -> bool:
        try:
            import pystray
        except ImportError:
            print("[Tray] pystray not installed — tray icon disabled. "
                  "Run: pip install pystray pillow")
            return False

        try:
            self._icon = pystray.Icon(
                "apex_resident",
                icon=_make_icon_image(self._state),
                title="Apex (idle)",
                menu=pystray.Menu(
                    *([pystray.MenuItem('Screen Companion', lambda _i, _m: self._on_companion())]
                      if self._on_companion else []),
                    pystray.MenuItem("Wake now", lambda _i, _m: self._on_wake_now()),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Mute 15 min", lambda _i, _m: self._on_mute(15)),
                    pystray.MenuItem("Mute 1 hour", lambda _i, _m: self._on_mute(60)),
                    pystray.MenuItem("Mute until quit", lambda _i, _m: self._on_mute(-1)),
                    pystray.MenuItem("Unmute", lambda _i, _m: self._on_mute(0)),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Open dashboard", lambda _i, _m: self._on_open_dashboard()),
                    pystray.MenuItem("Show recent activity", lambda _i, _m: self._on_show_recent()),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Quit Apex", lambda _i, _m: self._on_quit()),
                ),
            )
            # run_detached so it doesn't block the main thread
            self._icon.run_detached()
            print("[Tray] Icon running.")
            return True
        except Exception as e:
            print(f"[Tray] Failed to start (likely no display server): {e}")
            return False

    def set_state(self, state: str, tooltip: Optional[str] = None) -> None:
        """Update the icon to reflect a new state. Safe to call from any thread."""
        self._state = state
        if self._icon is None:
            return
        try:
            self._icon.icon = _make_icon_image(state)
            self._icon.title = tooltip or f"Apex ({state})"
        except Exception:
            pass

    def notify(self, title: str, message: str) -> None:
        """Show a desktop notification via the tray icon (or fallback)."""
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
                return
            except Exception:
                pass
        # Fallback: plyer
        try:
            from plyer import notification
            notification.notify(title=title, message=message, timeout=4)
        except Exception:
            print(f"[Tray] {title}: {message}")

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
