"""Read Apex's own text files whatever encoding they were saved in.

Python on Windows reads and writes text as cp1252 unless it runs in UTF-8 mode,
and Apex runs both ways depending on the launcher. A memory file written as
UTF-8 by one launch (a curly quote is E2 80 9D) then stopped the next launch
with "'charmap' codec can't decode byte 0x9d", and a file written as cp1252
would do the same in reverse. Apex now always writes UTF-8 and reads with this:
UTF-8 (with or without a BOM) first, then cp1252 for files saved the old way.

Imports nothing from Apex, so anything can use it.
"""
from __future__ import annotations

from pathlib import Path


def decode(raw: bytes, *, truncated: bool = False) -> str:
    """UTF-8 (BOM or not), else cp1252. `truncated`: raw was cut at a byte limit,
    so an incomplete UTF-8 character at its very end is dropped, not a reason
    to fall back to cp1252."""
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        if truncated and exc.reason == 'unexpected end of data':
            try:
                return raw[:exc.start].decode('utf-8-sig')
            except UnicodeDecodeError:
                pass
        return raw.decode('cp1252', errors='replace')


def read_text(path, limit: int | None = None) -> str:
    """The text of `path` (raises like Path.read_text if it can't be read).
    With `limit`, only the first `limit` bytes are read."""
    if limit is None:
        return decode(Path(path).read_bytes())
    with open(path, 'rb') as stream:
        raw = stream.read(limit + 1)
    return decode(raw[:limit], truncated=len(raw) > limit)
