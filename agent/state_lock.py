"""Hermes-backed, per-path cross-process leases for participating SQLite writers."""
from contextlib import contextmanager
from pathlib import Path
import threading

from agent._vendor.hermes import file_lock

_holders = {}
_guard = threading.Lock()


@contextmanager
def writer(path, timeout=10):
    if file_lock.fcntl is None and file_lock.msvcrt is None:
        raise RuntimeError("A kernel file lock is required for this persistent writer.")
    target = str(Path(path).expanduser().resolve())
    with _guard:
        holder = _holders.setdefault(target, threading.local())
    with file_lock._file_lock(Path(target + ".write.lock"), holder, timeout,
                              "Persistent writer lease timed out; operation was not retried"):
        yield
