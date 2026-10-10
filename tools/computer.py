"""Screen capture and native input: X11 xdotool or Windows/macOS PyAutoGUI.

Successful responses describe input sent, not verification of an application's
result. Callers should inspect a new screenshot to verify the resulting state.
"""
import base64
import io
import os
import platform
import subprocess
import config


class ComputerUnavailable(RuntimeError):
    """This host cannot provide the requested native desktop control."""


class ComputerControlError(RuntimeError):
    """Input failed, potentially after part of a compound action was sent."""


def _coordinates(*values):
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise ValueError("Desktop coordinates must be integers.")


def _native_backend():
    system = platform.system()
    if system == "Linux":
        if os.getenv("XDG_SESSION_TYPE", "").lower() == "wayland" or os.getenv("WAYLAND_DISPLAY"):
            raise ComputerUnavailable("Native control requires an X11 session; Wayland is unsupported.")
        if not os.getenv("DISPLAY"):
            raise ComputerUnavailable("Native control requires an active desktop (DISPLAY is unset).")
        return None
    if system not in {"Windows", "Darwin"}:
        raise ComputerUnavailable(f"Native control is unsupported on {system}.")
    try:
        import pyautogui
    except Exception as exc:
        raise ComputerUnavailable("PyAutoGUI is unavailable. Install desktop requirements and use an active desktop session.") from exc
    # Do not disable PyAutoGUI's fail-safe or retry its failures.
    return pyautogui


def _xdo(*args, timeout=10) -> str:
    try:
        result = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ComputerUnavailable("Install xdotool to use native control on X11.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ComputerControlError("xdotool timed out; input may have been partially sent. Inspect the screen before retrying.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()[:300]
        raise ComputerControlError(f"xdotool failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def screenshot(region=None) -> tuple[str, tuple[int, int]]:
    """Capture screen. Returns (base64_jpeg, (width, height))."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[1] if region is None else region
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=config.SCREENSHOT_QUALITY)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return b64, img.size


def click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    _coordinates(x, y)
    if not isinstance(double, bool):
        raise ValueError("double must be a boolean.")
    btn_map = {"left": "1", "middle": "2", "right": "3"}
    if button not in btn_map:
        raise ValueError("button must be left, middle or right.")
    backend = _native_backend()
    if backend is not None:
        backend.click(x=x, y=y, button=button, clicks=2 if double else 1, interval=0.1 if double else 0)
    else:
        _xdo("mousemove", "--", str(x), str(y))
        if double:
            _xdo("click", "--repeat", "2", "--delay", "100", btn_map[button])
        else:
            _xdo("click", btn_map[button])
    return f"Sent {'double ' if double else ''}{button} click at ({x}, {y})"


def right_click(x: int, y: int) -> str:
    return click(x, y, button="right")


def move_mouse(x: int, y: int) -> str:
    _coordinates(x, y)
    backend = _native_backend()
    if backend is not None:
        backend.moveTo(x, y)
    else:
        _xdo("mousemove", "--sync", "--", str(x), str(y))
    return f"Moved mouse to ({x}, {y})"


def type_text(text: str, delay_ms: int = 30) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    if not isinstance(delay_ms, int) or isinstance(delay_ms, bool) or delay_ms < 0:
        raise ValueError("delay_ms must be a non-negative integer.")
    backend = _native_backend()
    if backend is None:
        _xdo("type", f"--delay={delay_ms}", "--", text, timeout=max(10, len(text) * delay_ms / 1000 + 5))
    elif platform.system() == "Windows":
        from tools.windows_input import type_unicode
        type_unicode(text, delay_ms, check_abort=backend.failSafeCheck)
    else:
        if any(ord(char) > 127 or char.lower() not in backend.KEYBOARD_KEYS for char in text):
            raise ComputerUnavailable("Native macOS typing supports ASCII only; use browser input for Unicode text.")
        backend.write(text, interval=delay_ms / 1000)
    return f"Sent text input: {text!r}"


def hotkey(*keys: str) -> str:
    if not keys or any(not isinstance(key, str) or not key.strip() for key in keys):
        raise ValueError("Provide at least one non-empty hotkey key.")
    combo = "+".join(keys)
    backend = _native_backend()
    if backend is None:
        _xdo("key", "--", combo)
    else:
        aliases = {"control": "ctrl", "control_l": "ctrlleft", "control_r": "ctrlright",
                   "shift_l": "shiftleft", "shift_r": "shiftright", "alt_l": "altleft", "alt_r": "altright",
                   "return": "enter", "escape": "esc", "super": "command" if platform.system() == "Darwin" else "win",
                   "meta": "command" if platform.system() == "Darwin" else "win"}
        normalized = [aliases.get(key.lower(), key.lower()) for key in keys]
        if any(key not in backend.KEYBOARD_KEYS for key in normalized):
            raise ValueError(f"Unsupported native hotkey: {combo}")
        backend.hotkey(*normalized)
    return f"Sent hotkey: {combo}"


def scroll(x: int, y: int, clicks: int) -> str:
    _coordinates(x, y)
    if not isinstance(clicks, int) or isinstance(clicks, bool):
        raise ValueError("Scroll clicks must be an integer.")
    backend = _native_backend()
    if backend is not None:
        backend.scroll(clicks, x=x, y=y)
    else:
        _xdo("mousemove", "--", str(x), str(y))
        btn = "4" if clicks > 0 else "5"  # 4=scroll up, 5=scroll down
        for _ in range(abs(clicks)):
            _xdo("click", btn)
    direction = "up" if clicks > 0 else "down"
    return f"Sent scroll {direction} {abs(clicks)} clicks at ({x}, {y})"


def drag(x1: int, y1: int, x2: int, y2: int) -> str:
    _coordinates(x1, y1, x2, y2)
    backend = _native_backend()
    if backend is not None:
        backend.moveTo(x1, y1)
        backend.dragTo(x2, y2, duration=0.3, button="left")
    else:
        _xdo("mousemove", "--", str(x1), str(y1))
        _xdo("mousedown", "1")
        try:
            _xdo("mousemove", "--sync", "--", str(x2), str(y2))
        finally:
            _xdo("mouseup", "1")
    return f"Sent drag from ({x1}, {y1}) to ({x2}, {y2})"


def get_screen_size() -> tuple[int, int]:
    backend = _native_backend()
    if backend is not None:
        w, h = backend.size()
    else:
        out = _xdo("getdisplaygeometry")
        try:
            w, h = map(int, out.split())
        except (ValueError, TypeError) as exc:
            raise ComputerControlError("xdotool returned invalid screen geometry.") from exc
    if w <= 0 or h <= 0:
        raise ComputerControlError("The native desktop returned invalid screen geometry.")
    return int(w), int(h)
