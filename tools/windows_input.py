"""Windows Unicode keyboard input without changing the user's clipboard."""
import ctypes
import time


# Fixed-width Win32 types also allow offline ABI tests on non-Windows hosts.
class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", _InputUnion)]


def _events(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for char in text:
        if char in {"\n", "\t"}:
            yield [(_KeyboardInput(13 if char == "\n" else 9, 0, flags, 0, 0)) for flags in (0, 2)]
        else:
            units = char.encode("utf-16-le")
            yield [_KeyboardInput(0, int.from_bytes(units[i:i + 2], "little"), flags, 0, 0)
                   for i in range(0, len(units), 2) for flags in (4, 6)]


def type_unicode(text: str, delay_ms: int, *, check_abort=None) -> None:
    from tools.computer import ComputerControlError
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send = user32.SendInput
    send.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Input), ctypes.c_int]
    send.restype = ctypes.c_uint32
    for keys in _events(text):
        if check_abort is not None:
            check_abort()
        events = (_Input * len(keys))(*[_Input(1, _InputUnion(ki=key)) for key in keys])
        sent = send(len(events), events, ctypes.sizeof(_Input))
        if sent != len(events):
            # Release keys even when the OS accepts only part of a batch.
            releases = (_Input * len(keys))(*[_Input(1, _InputUnion(ki=_KeyboardInput(
                key.wVk, key.wScan, key.dwFlags | 2, 0, 0))) for key in keys])
            send(len(releases), releases, ctypes.sizeof(_Input))
            raise ComputerControlError("Windows rejected keyboard input; text may be partial. Inspect the focused app and its permissions before retrying.")
        if delay_ms:
            time.sleep(delay_ms / 1000)
