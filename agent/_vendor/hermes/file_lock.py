"""Verbatim Hermes advisory file-lock helpers; see NOTICE."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import threading
import time
from typing import Any
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

def _kernel_lock(lock_file: Any, acquire: bool) -> None:
    """Non-blocking exclusive flock (fcntl) or 1-byte msvcrt lock at offset 0; ``acquire=False`` releases."""
    if fcntl:
        fcntl.flock(lock_file.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if acquire else fcntl.LOCK_UN)
    else:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)


@contextmanager
def _file_lock(
    lock_path: Path, holder: threading.local, timeout_seconds: float, timeout_message: str):
    """Cross-process advisory flock helper, reentrant per-thread via ``holder.depth``.

    Falls back to a depth-only guard when neither ``fcntl`` nor ``msvcrt`` is available. Callers
    supply their own ``threading.local`` so independent locks (profile store vs global root vs the
    shared Nous store) track reentrancy separately."""
    if getattr(holder, "depth", 0) > 0:
        holder.depth += 1
        try:
            yield
        finally:
            holder.depth -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        lock_file = None
        if fcntl is not None or msvcrt is not None:
            # msvcrt.locking needs a non-empty file with the pointer at 0. This convenience write can
            # race another holder's byte-range lock and raise PermissionError (reproduced with 20
            # concurrent processes on Windows); losing the race just means the file already has
            # content, so swallow it.
            if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
                try:
                    lock_path.write_text(" ", encoding="utf-8")
                except (OSError, PermissionError):
                    pass
            lock_file = stack.enter_context(lock_path.open("r+" if msvcrt else "a+", encoding="utf-8"))
            deadline = time.monotonic() + max(1.0, timeout_seconds)
            while True:
                try:
                    _kernel_lock(lock_file, True)
                    break
                except (BlockingIOError, OSError, PermissionError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(timeout_message)
                    time.sleep(0.05)

        holder.depth = 1
        try:
            yield
        finally:
            holder.depth = 0
            if lock_file is not None:
                try:
                    _kernel_lock(lock_file, False)
                except (OSError, IOError):
                    pass


