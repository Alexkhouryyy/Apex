"""User-started dictation pinned to one Windows window and focused control."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import threading
from functools import lru_cache


class FocusInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('active', wintypes.HWND), ('focus', wintypes.HWND),
                ('capture', wintypes.HWND), ('menu', wintypes.HWND),
                ('move', wintypes.HWND), ('caret', wintypes.HWND), ('rect', wintypes.RECT)]


@lru_cache(maxsize=1)
def _automation_class():
    import clr
    clr.AddReference('UIAutomationClient'); clr.AddReference('UIAutomationTypes')
    from System.Windows.Automation import AutomationElement
    return AutomationElement


def current_target():
    if os.name != 'nt':
        raise RuntimeError('Writing into another app requires Windows.')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(FocusInfo)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    hwnd = user.GetForegroundWindow()
    pid = wintypes.DWORD()
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    info = FocusInfo(); info.cbSize = ctypes.sizeof(info)
    if not hwnd or not thread or not user.GetGUIThreadInfo(thread, ctypes.byref(info)) or not info.focus:
        raise RuntimeError('Click the field you want to write in, then press F8.')
    title = ctypes.create_unicode_buffer(256)
    user.GetWindowTextW(hwnd, title, len(title))
    # A browser can switch HTML inputs while keeping the same native HWND.
    # UI Automation distinguishes those fields and identifies password input.
    try:
        element = _automation_class().FocusedElement
        if element is None or element.Current.IsPassword:
            raise RuntimeError('Select a regular text field before starting dictation.')
        element_id = [int(part) for part in element.GetRuntimeId()]
        if not element_id: raise RuntimeError('The selected field could not be identified.')
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError('Windows could not identify the selected field. Reopen the app and try again.') from exc
    return {'window': int(hwnd), 'focus': int(info.focus), 'pid': pid.value,
            'element_id':element_id, 'title': title.value or 'Selected app'}


def safe_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError('Dictation needs between 1 and 20000 characters.')
    # Never send Enter or Tab: those can submit a form or move to another field.
    text = text.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    if any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 or 0xd800 <= ord(c) <= 0xdfff for c in text):
        raise ValueError('Dictation contains unsupported control characters.')
    return text


class Session:
    def __init__(self, target, generation):
        self.target, self.generation = target, generation
        self.cancelled = threading.Event()
        self.lock = threading.Lock()

    def stop(self):
        self.cancelled.set()

    def insert(self, text, generation):
        text = safe_text(text)
        if type(generation) is not int:
            raise ValueError('Invalid dictation session.')
        def check():
            if self.cancelled.is_set() or generation != self.generation:
                raise RuntimeError('Dictation was stopped. The remaining text was not inserted.')
            now = current_target()
            if any(now[k] != self.target[k] for k in ('window', 'focus', 'pid', 'element_id')):
                self.stop()
                raise RuntimeError('The selected field changed. Text may be partial; review it before starting dictation again.')
        with self.lock:
            check()
            from tools.windows_input import type_unicode
            type_unicode(text, 0, check_abort=check, batch_size=32)
        return {'inserted': True, 'characters': len(text)}
