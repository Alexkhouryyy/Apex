"""Global hotkey wrapper around pynput.

Press Ctrl+Space → wake Apex without speaking. Press Ctrl+Alt+M → toggle mute.
Both default combos are configurable via RESIDENT_GLOBAL_HOTKEY and
RESIDENT_MUTE_HOTKEY in config.py.

Degrades gracefully when pynput isn't installed or running headless — caller
just sees `start()` return False and continues.
"""
from typing import Callable, Optional
import queue
import threading
import os
import re


class WindowsFunctionHotkey:
    """Register one unmodified function key, consuming it instead of forwarding it to the active app."""
    def __init__(self, combo, callback):
        self.number = int(re.fullmatch(r'<f(\d+)>', combo).group(1))
        self.callback, self.thread_id, self.error = callback, None, None
        self.ready = threading.Event()
        self.thread = None

    def start(self):
        import ctypes
        from ctypes import wintypes
        def listen():
            user = ctypes.WinDLL('user32', use_last_error=True)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentThreadId.restype = wintypes.DWORD
            user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user.RegisterHotKey.restype = wintypes.BOOL
            user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user.GetMessageW.restype = wintypes.BOOL
            user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            message = wintypes.MSG()
            self.thread_id = kernel.GetCurrentThreadId()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            if not user.RegisterHotKey(None, 1, 0x4000, 0x70 + self.number - 1):
                self.error = OSError(ctypes.get_last_error(), 'Function hotkey is already in use or unavailable')
                self.ready.set()
                return
            self.ready.set()
            try:
                while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    if message.message == 0x0312:
                        self.callback()
            finally:
                user.UnregisterHotKey(None, 1)
        self.thread = threading.Thread(target=listen, daemon=True, name='ApexFunctionHotkey')
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError('Windows hotkey did not initialize.')
        if self.error:
            raise self.error

    def stop(self):
        if self.thread_id:
            import ctypes
            from ctypes import wintypes
            user = ctypes.windll.user32
            user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


class GlobalHotkeys:
    """Owns a single pynput GlobalHotKeys listener with multiple bindings."""

    def __init__(self) -> None:
        self._listener: Optional[object] = None
        self._bindings: dict[str, Callable[[], None]] = {}
        self._pending = queue.Queue(maxsize=8)
        self._stopped = threading.Event()
        self._worker = None

    def bind(self, combo: str, callback: Callable[[], None]) -> None:
        """Register a combo like '<ctrl>+<space>'. Must be called before start()."""
        self._bindings[combo] = callback

    def start(self) -> bool:
        if not self._bindings:
            return False
        try:
            # Windows removes slow keyboard hooks. Capture/GUI/model work must
            # run on a worker, with presses kept in order and a bounded queue.
            self._stopped.clear()
            def enqueue(callback):
                def pressed():
                    try:
                        self._pending.put_nowait(callback)
                    except queue.Full:
                        pass
                return pressed
            def work():
                while not self._stopped.is_set():
                    try:
                        callback = self._pending.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if self._stopped.is_set():
                        break
                    try:
                        callback()
                    except Exception as exc:
                        print(f'[Hotkey] Action failed: {exc}')
            self._worker = threading.Thread(target=work, daemon=True, name='ApexHotkeyActions')
            self._worker.start()
            if os.name == 'nt' and len(self._bindings) == 1 and re.fullmatch(r'<f(?:[1-9]|1\d|2[0-4])>', next(iter(self._bindings))):
                combo, fn = next(iter(self._bindings.items()))
                self._listener = WindowsFunctionHotkey(combo, enqueue(fn))
            else:
                from pynput import keyboard
                self._listener = keyboard.GlobalHotKeys({combo: enqueue(fn) for combo, fn in self._bindings.items()})
            self._listener.start()
            combos = ", ".join(self._bindings.keys())
            print(f"[Hotkey] Listening for: {combos}")
            return True
        except Exception as e:
            self.stop()
            print(f"[Hotkey] Failed to start (likely no display / Wayland): {e}")
            return False

    def stop(self) -> None:
        self._stopped.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
