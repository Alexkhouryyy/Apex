"""Keep Apex's native help surfaces out of subsequent screen snapshots."""
import ctypes
from ctypes import wintypes
import os


def exclude_from_capture(hwnd):
    if os.name != 'nt':
        return False
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.SetWindowDisplayAffinity.restype = wintypes.BOOL
    # Windows 10 2004+: WDA_EXCLUDEFROMCAPTURE. Applies to our own top-level
    # windows; no capture settings or other applications are changed.
    return bool(user.SetWindowDisplayAffinity(hwnd, 0x11))
