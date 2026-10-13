"""Keep Apex's native help surfaces out of subsequent screen snapshots."""
import ctypes
from ctypes import wintypes
import os
import time
import threading
from contextlib import contextmanager
_capture_lock=threading.RLock()


class ProcessEntry(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                ('name',wintypes.WCHAR*260)]


def process_parents():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes,kernel.CreateToolhelp32Snapshot.restype=[wintypes.DWORD,wintypes.DWORD],wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:raise OSError('Windows process ownership could not be checked.')
    try:
        entry=ProcessEntry();entry.size=ctypes.sizeof(entry);parents={}
        found=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while found and len(parents)<10000:
            parents[entry.pid]=entry.parent
            found=kernel.Process32NextW(handle,ctypes.byref(entry))
        return parents
    finally:kernel.CloseHandle(handle)


def belongs_to(pid,root,parents):
    seen=set()
    while pid and pid not in seen:
        if pid==root:return True
        seen.add(pid);pid=parents.get(pid,0)
    return False


def window_pid(hwnd):
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    pid=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    return pid.value


def owned_window(hwnd,root_pid):
    return os.name=='nt' and belongs_to(window_pid(hwnd),root_pid,process_parents())


@contextmanager
def without_surfaces(handles,root_pid,restore=lambda:True):
    with _capture_lock:
        with _hidden_surfaces(handles,root_pid,restore):
            yield


@contextmanager
def _hidden_surfaces(handles,root_pid,restore):
    """Hide only verified descendants of our UI, then restore visible surfaces.

    Display affinity alone is not honored by every GDI/remote capture path.
    User sketch marks are deliberately never registered here.
    """
    if os.name!='nt' or not handles or not root_pid:
        yield;return
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.IsWindow.argtypes=[wintypes.HWND]
    user.ShowWindowAsync.argtypes=[wintypes.HWND,ctypes.c_int]
    user.GetForegroundWindow.restype=wintypes.HWND
    user.SetForegroundWindow.argtypes=[wintypes.HWND]
    parents=process_parents();hidden=[];foreground=user.GetForegroundWindow()
    try:
        for hwnd in dict.fromkeys(handles):
            pid=window_pid(hwnd)
            if belongs_to(pid,root_pid,parents) and user.IsWindowVisible(hwnd):
                hidden.append((hwnd,pid));user.ShowWindowAsync(hwnd,0)
        deadline=time.monotonic()+1
        while any(user.IsWindowVisible(hwnd) for hwnd,_ in hidden):
            if time.monotonic()>deadline:raise RuntimeError('Apex could not hide its help surfaces for the snapshot.')
            time.sleep(.01)
        try:ctypes.WinDLL('dwmapi').DwmFlush()
        except (AttributeError,OSError):pass
        underlying=user.GetForegroundWindow()
        yield
    finally:
        if restore():
            visible=[]
            for hwnd,pid in hidden:
                if user.IsWindow(hwnd) and window_pid(hwnd)==pid:
                    user.ShowWindowAsync(hwnd,4);visible.append(hwnd)
            deadline=time.monotonic()+1
            while any(not user.IsWindowVisible(hwnd) for hwnd in visible) and time.monotonic()<deadline:
                time.sleep(.01)
            if hidden and foreground and 'underlying' in locals() and user.GetForegroundWindow()==underlying:
                if belongs_to(window_pid(foreground),root_pid,parents) and user.IsWindowVisible(foreground):
                    user.SetForegroundWindow(foreground)


def exclude_from_capture(hwnd):
    if os.name != 'nt':
        return False
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.SetWindowDisplayAffinity.restype = wintypes.BOOL
    # Windows 10 2004+: WDA_EXCLUDEFROMCAPTURE. Applies to our own top-level
    # windows; no capture settings or other applications are changed.
    return bool(user.SetWindowDisplayAffinity(hwnd, 0x11))
