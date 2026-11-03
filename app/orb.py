"""Desktop orb — floating always-on-top window wrapping the Apex dashboard.

Activated via python main.py --resident when ORB_ENABLED=true in .env.

Wraps the dashboard URL in a pywebview window:
  - 440×640 expanded, 140×140 collapsed
  - Frameless, always-on-top
  - System tray (pystray): Show/Hide/Open-in-browser/Quit
  - Global hotkey (pynput): DESKTOP_SHELL_HOTKEY toggles expand/collapse
  - Window position persisted to ~/Documents/Apex/window.json

Requires: pywebview, pystray, pynput (pip install pywebview pystray pynput pillow)
"""
from __future__ import annotations

import text_file as _text_file
import json
import threading
from pathlib import Path
from typing import Optional

try:
    import webview
    _WEBVIEW = True
except ImportError:
    _WEBVIEW = False

try:
    import pystray
    from PIL import Image as _PIL_Image
    _TRAY = True
except ImportError:
    _TRAY = False

try:
    from pynput import keyboard as _kb
    _PYNPUT = True
except ImportError:
    _PYNPUT = False

_WINDOW_STATE_FILE = Path.home() / "Documents" / "Apex" / "window.json"
_EXPANDED_W, _EXPANDED_H = 440, 640
_COLLAPSED_W, _COLLAPSED_H = 140, 140


def _load_state() -> dict:
    try:
        return json.loads(_text_file.read_text(_WINDOW_STATE_FILE)) if _WINDOW_STATE_FILE.exists() else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _WINDOW_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _WINDOW_STATE_FILE.write_text(json.dumps(state), encoding='utf-8')


def _make_icon():
    try:
        return _PIL_Image.new("RGBA", (64, 64), (30, 30, 60, 255))
    except Exception:
        return None


class ApexOrb:
    def __init__(self, url: str, hotkey_str: str):
        self.url = url
        self.hotkey_str = hotkey_str
        self._window: Optional[object] = None
        self._expanded = True
        self._tray = None

    def _toggle(self):
        if self._window is None:
            return
        if self._expanded:
            self._window.resize(_COLLAPSED_W, _COLLAPSED_H)
        else:
            self._window.resize(_EXPANDED_W, _EXPANDED_H)
        self._expanded = not self._expanded

    def _start_hotkey(self):
        if not _PYNPUT:
            return
        try:
            from pynput.keyboard import GlobalHotKeys
            GlobalHotKeys({self.hotkey_str: self._toggle}).start()
        except Exception as e:
            print(f"[Orb] Hotkey setup failed: {e}")

    def _start_tray(self):
        if not _TRAY:
            return
        icon_img = _make_icon()
        if icon_img is None:
            return

        def on_show(_i, _it):
            if self._window:
                self._window.resize(_EXPANDED_W, _EXPANDED_H)
                self._expanded = True

        def on_hide(_i, _it):
            if self._window:
                self._window.resize(_COLLAPSED_W, _COLLAPSED_H)
                self._expanded = False

        def on_browser(_i, _it):
            import webbrowser
            webbrowser.open(self.url)

        def on_quit(_i, _it):
            _i.stop()
            if self._window:
                self._window.destroy()

        menu = pystray.Menu(
            pystray.MenuItem("Show Apex", on_show),
            pystray.MenuItem("Hide", on_hide),
            pystray.MenuItem("Open in browser", on_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )
        self._tray = pystray.Icon("Apex", icon_img, "Apex AI", menu)
        threading.Thread(target=self._tray.run, daemon=True, name="ApexTray").start()

    def run(self):
        if not _WEBVIEW:
            print("[Orb] pywebview not installed. Run: pip install pywebview pystray pynput pillow")
            return

        state = _load_state()
        w = state.get("w", _EXPANDED_W)
        h = state.get("h", _EXPANDED_H)

        self._window = webview.create_window(
            "Apex",
            self.url,
            width=w,
            height=h,
            x=state.get("x"),
            y=state.get("y"),
            frameless=True,
            on_top=True,
            background_color="#000000",
        )
        self._start_hotkey()
        self._start_tray()

        def on_closed():
            try:
                geo = self._window.get_position() if self._window else {}
                _save_state({"w": w, "h": h, "x": geo.get("x"), "y": geo.get("y")})
            except Exception:
                pass

        webview.start(on_closed, debug=False)


def run_orb(dashboard_url: str = "http://localhost:7860", hotkey: str = "<ctrl>+<shift>+\\"):
    """Entry point — blocks until the orb window is closed."""
    ApexOrb(url=dashboard_url, hotkey_str=hotkey).run()
