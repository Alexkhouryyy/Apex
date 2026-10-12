"""Reverse-engineering workspace: Apex drives REA (github.com/morluto/rea, MIT)
as a read-only MCP client, so you can open software you own and see how it works.

This is the engine behind the Reverse page (dashboard/reverse.py). It is the
owner's: every route that reaches it is master-token only. It never mutates the
target or the analysis, launches the program, touches the network or writes
files: it calls only REA's read tools (READ_TOOLS), and refuses any other, so a
model or a bad request can't turn an inspection into an action. REA itself opens
the program in Ghidra in an ephemeral, throwaway project; the file on disk is
never written.

Each open binary gets one long-lived REA process (a Connection) that holds the
Ghidra session, so drilling into a function doesn't re-import the file. The
process is closed when you close the session, when it goes idle, or when Apex
restarts. At most MAX_SESSIONS run at once.

Apex can't verify REA's exact result shapes from here (no Ghidra in CI), so every
result is parsed leniently (_parse): JSON when REA returns JSON, otherwise the raw
text. The page renders whatever comes back. Tests drive a fake REA (a fake binary)
through the same path; a real session on your PC is what confirms the live shapes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from agent import code_brain

import config


class ReverseError(ValueError):
    """A request the page made that can't be done, in plain words for the owner."""


# REA tools Apex will call: read-only native analysis only. open_binary imports
# the file into Ghidra (a launched helper, no target execution); close_binary
# tidies up. Everything that captures, observes, installs, pushes, starts, sets,
# annotates, extracts or writes is deliberately absent, so this workspace can
# only ever read.
READ_TOOLS = frozenset({
    'open_binary', 'close_binary', 'binary_overview', 'binary_session',
    'list_procedures', 'list_names', 'list_segments', 'list_strings', 'list_architectures',
    'search_procedures', 'search_strings', 'find_xrefs_to_name', 'get_call_graph',
    'procedure_info', 'procedure_address', 'procedure_assembly', 'procedure_pseudo_code',
    'procedure_callees', 'procedure_callers', 'procedure_references',
    'read_bytes', 'read_function_instructions', 'resolve_containing_procedure',
    'resolve_native_call_targets', 'xrefs', 'analyze_function', 'inspect_pe_resources',
    'inspect_binary_layout',
})

MAX_SESSIONS = 2            # Ghidra sessions are heavy; two open binaries at once
MAX_FILE_BYTES = 2_000_000_000   # 2 GB: a guard against pointing it at a disk image
OPEN_TIMEOUT = 600         # Ghidra's first import of a binary can take minutes
CALL_TIMEOUT = 300
IDLE_SECONDS = 1800        # close an untouched REA process after 30 minutes
LIST_CAP = 400             # functions/strings returned to the page at once

# A chosen file must sit under one of the owner's own roots, never a system
# directory, so a stray path can't point REA at Windows itself. On Windows every
# fixed drive is a root; elsewhere it is the home directory.
_WINDOWS = os.name == 'nt'


def _roots() -> list[Path]:
    if _WINDOWS:
        return [Path(f'{d}:\\') for d in 'CDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{d}:\\').exists()]
    return [Path.home().resolve(), Path('/mnt'), Path('/media'), Path('/Volumes')]


def _forbidden(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    if _WINDOWS:
        bad = {'windows', 'program files', 'program files (x86)', 'programdata', '$recycle.bin'}
        return any(p in bad for p in parts)
    return path.parts[:2] in (('/', 'etc'), ('/', 'bin'), ('/', 'sbin'), ('/', 'usr'),
                              ('/', 'boot'), ('/', 'proc'), ('/', 'sys'), ('/', 'dev'))


def _check_path(raw: str) -> Path:
    text = str(raw or '').strip().strip('"')
    if not text:
        raise ReverseError('Choose a file to open.')
    try:
        path = Path(text).expanduser()
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReverseError(f'That path cannot be read: {exc}') from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ReverseError('That is not a file on this PC.')
    if _forbidden(resolved):
        raise ReverseError('For your safety, Apex will not open a file from a system folder. '
                           'Copy it somewhere like your Downloads first.')
    roots = _roots()
    if roots and not any(_within(resolved, r) for r in roots):
        raise ReverseError('Open a file from your own drives, not a system or network location.')
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise ReverseError(f'That file cannot be read: {exc}') from exc
    if size <= 0:
        raise ReverseError('That file is empty.')
    if size > MAX_FILE_BYTES:
        raise ReverseError(f'That file is larger than {MAX_FILE_BYTES // 1_000_000_000} GB; '
                           'this workspace is for programs, not disk images.')
    return resolved


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _parse(text):
    """REA returns JSON for most read tools; fall back to raw text when it doesn't.
    Apex never guesses a shape here — the page renders whatever this yields."""
    if isinstance(text, (dict, list)):
        return text
    s = str(text if text is not None else '').strip()
    if not s:
        return {'text': ''}
    try:
        return json.loads(s)
    except ValueError:
        return {'text': s[:200000]}


# ---------------------------------------------------------------- the REA process

def rea_ready() -> bool:
    """Whether REA can be launched on this PC (npx present, package pinned)."""
    try:
        return bool(code_brain.rea_server(getattr(config, 'CODE_REA_PACKAGE', '')))
    except Exception:
        return False


def _rea_config() -> dict:
    """REA's launch config (reused from Apex Code), or a clear error when npx or a
    pinned package isn't there."""
    server = code_brain.rea_server(getattr(config, 'CODE_REA_PACKAGE', ''))
    if not server:
        raise ReverseError("REA isn't set up on this PC. Install Node.js 22.19+ and run "
                           "`npx rea-agents@6.4.0 setup`, then reopen this page.")
    return server


def _connect(server: dict, timeout: float):
    """Open one long-lived REA process and return a Connection. Replaced in tests
    by a fake REA so the whole workspace is exercised without Ghidra."""
    from agent import reverse_rea
    return reverse_rea.open_connection(server, timeout)


_lock = threading.Lock()
_sessions: dict[int, dict] = {}
_conns: dict[int, object] = {}
_next_id = 1


def _touch(sid: int) -> None:
    s = _sessions.get(sid)
    if s:
        s['used'] = time.time()


def _call(sid: int, tool: str, inputs: dict | None = None, timeout: float = CALL_TIMEOUT):
    """One REA read tool against this session's open binary. Refuses any tool that
    is not in the read-only allowlist — the single gate that keeps this to reading."""
    if tool not in READ_TOOLS:
        raise ReverseError(f'{tool} is not a read-only inspection tool.')
    with _lock:
        conn = _conns.get(sid)
        if not conn or sid not in _sessions:
            raise ReverseError('That inspection is closed. Open the file again.')
        _touch(sid)
    try:
        raw = conn.call(tool, dict(inputs or {}), timeout)
    except TimeoutError as exc:
        raise ReverseError(f'REA took too long on {tool}. The file may be very large.') from exc
    except Exception as exc:                 # REA/transport failure: plain words, never a stack
        raise ReverseError(f'REA could not run {tool}: {str(exc)[:300]}') from exc
    return _parse(raw)


def _handle(result) -> str:
    """REA names the open binary with some handle (a path, id or document key);
    pass back whatever open_binary gave so later calls name the same target."""
    if isinstance(result, dict):
        for key in ('handle', 'binary', 'document', 'id', 'path', 'target'):
            if result.get(key):
                return str(result[key])
    return ''


def open_binary(path: str) -> dict:
    """Open a file the owner chose in a fresh REA/Ghidra session and return the
    session with a first overview. Read-only; the file on disk is never changed."""
    global _next_id
    resolved = _check_path(path)
    server = _rea_config()
    with _lock:
        _reap_idle_locked()
        if len(_sessions) >= MAX_SESSIONS:
            raise ReverseError(f'{MAX_SESSIONS} inspections are already open. Close one first.')
        sid = _next_id
        _next_id += 1
    conn = None
    try:
        conn = _connect(server, OPEN_TIMEOUT)
        opened = _parse(conn.call('open_binary', {'path': str(resolved)}, OPEN_TIMEOUT))
        with _lock:
            _conns[sid] = conn
            _sessions[sid] = {'id': sid, 'path': str(resolved), 'name': resolved.name,
                              'handle': _handle(opened), 'opened': time.time(), 'used': time.time()}
    except ReverseError:
        if conn:
            _safe_close(conn)
        raise
    except Exception as exc:
        if conn:
            _safe_close(conn)
        raise ReverseError(f'REA could not open that file: {str(exc)[:300]}') from exc
    return session(sid)


def session(sid: int) -> dict:
    with _lock:
        s = _sessions.get(sid)
        if not s:
            raise ReverseError('That inspection is closed. Open the file again.')
        return dict(s)


def sessions() -> list[dict]:
    with _lock:
        _reap_idle_locked()
        return [dict(s) for s in sorted(_sessions.values(), key=lambda x: x['opened'], reverse=True)]


def overview(sid: int) -> dict:
    s = session(sid)
    out = {'session': s, 'overview': _call(sid, 'binary_overview'),
           'segments': _call(sid, 'list_segments'),
           'architectures': _call(sid, 'list_architectures')}
    return out


def functions(sid: int, query: str = '', limit: int = LIST_CAP) -> dict:
    session(sid)
    limit = max(1, min(int(limit or LIST_CAP), LIST_CAP))
    if str(query or '').strip():
        data = _call(sid, 'search_procedures', {'query': str(query)[:200], 'limit': limit})
    else:
        data = _call(sid, 'list_procedures', {'limit': limit})
    return {'query': str(query or ''), 'limit': limit, 'result': data}


def strings(sid: int, query: str = '', limit: int = LIST_CAP) -> dict:
    session(sid)
    limit = max(1, min(int(limit or LIST_CAP), LIST_CAP))
    if str(query or '').strip():
        data = _call(sid, 'search_strings', {'query': str(query)[:200], 'limit': limit})
    else:
        data = _call(sid, 'list_strings', {'limit': limit})
    return {'query': str(query or ''), 'limit': limit, 'result': data}


def function(sid: int, address: str) -> dict:
    session(sid)
    addr = str(address or '').strip()
    if not addr or len(addr) > 120:
        raise ReverseError('Give a function address or name to inspect.')
    out = {'address': addr, 'info': _call(sid, 'procedure_info', {'address': addr})}
    for key, tool in (('pseudocode', 'procedure_pseudo_code'), ('assembly', 'procedure_assembly'),
                      ('callers', 'procedure_callers'), ('callees', 'procedure_callees')):
        try:
            out[key] = _call(sid, tool, {'address': addr})
        except ReverseError as exc:          # one missing facet never sinks the view
            out[key] = {'error': str(exc)}
    return out


def close(sid: int) -> dict:
    with _lock:
        _sessions.pop(sid, None)
        conn = _conns.pop(sid, None)
    if conn:
        _safe_close(conn)
    return {'closed': sid}


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception as exc:
        print(f'[Reverse] could not close a REA session cleanly: {exc}')


def _reap_idle_locked() -> None:
    now = time.time()
    for sid in [s['id'] for s in _sessions.values() if now - s['used'] > IDLE_SECONDS]:
        _sessions.pop(sid, None)
        conn = _conns.pop(sid, None)
        if conn:
            threading.Thread(target=_safe_close, args=(conn,), daemon=True).start()


def shutdown() -> None:
    """Close every REA process (Apex is stopping or restarting)."""
    with _lock:
        conns = list(_conns.values())
        _conns.clear()
        _sessions.clear()
    for conn in conns:
        _safe_close(conn)
