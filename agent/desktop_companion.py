"""Owned desktop UI process and private, bounded command channel.

The dashboard token remains necessary when configured. The additional random
session key is passed only to our local UI child, never to a browser URL.
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
_condition = threading.Condition(threading.RLock())
_process = None
_session = ''
_events: list[dict] = []
_results: dict[int, dict] = {}
_seq = 0
_heartbeat = 0.0
_ready = False
_shown = False
_mode = 'idle'
_url = ''
_keys = None


def local_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != 'http' or parts.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('Desktop Companion requires a loopback HTTP dashboard URL.')
    return f'{parts.scheme}://{parts.netloc}'


def running() -> bool:
    with _condition:
        return _process is not None and _process.poll() is None


def status() -> dict:
    with _condition:
        alive = running()
        return {'running': alive, 'ready': alive and _ready and time.monotonic() - _heartbeat < 15,
                'shown': alive and _shown, 'mode': _mode if alive else 'idle', 'seq': _seq,
                'hotkey_ready': _keys is not None}


def authorize(session: str) -> bool:
    with _condition:
        return bool(_session and session and running() and hmac.compare_digest(session, _session))


def start(url: str) -> dict:
    global _process, _session, _ready, _heartbeat, _shown, _url, _mode, _keys
    url = local_url(url)
    with _condition:
        if running():
            return status()
        import importlib.util
        if importlib.util.find_spec('webview') is None:
            raise RuntimeError('Run Setup-Apex-Screen-Companion.cmd once to install the desktop window.')
        python = Path(sys.executable)
        if os.name == 'nt' and python.with_name('pythonw.exe').is_file():
            python = python.with_name('pythonw.exe')
        env = dict(os.environ)
        _session = secrets.token_urlsafe(32)
        env['APEX_DESKTOP_SESSION'] = _session
        _ready, _heartbeat, _shown = False, 0.0, False
        _url, _mode = url, 'idle'
        _events.clear()
        _results.clear()
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        _process = subprocess.Popen([str(python), '-m', 'app.companion_desktop', '--url', url,
                                     '--parent-pid', str(os.getpid())], cwd=ROOT, env=env,
                                    stdin=subprocess.DEVNULL, **kwargs)
        if _keys is not None:
            _keys.stop()
        from app.hotkey import GlobalHotkeys
        from agent import look_now
        import config
        keys = GlobalHotkeys()
        keys.bind(config.COMPANION_HOTKEY, look_now.on_hotkey)
        _keys = keys if keys.start() else None
        return status()


def poll(after: int) -> list[dict]:
    global _heartbeat
    with _condition:
        _heartbeat = time.monotonic()
        return [dict(item) for item in _events if item['seq'] > after]


def connected() -> None:
    global _ready, _heartbeat
    with _condition:
        _ready, _heartbeat = True, time.monotonic()
        _condition.notify_all()


def publish(kind: str, **payload) -> int:
    global _seq, _shown
    with _condition:
        if not running():
            raise RuntimeError('Desktop Companion is not running. Run its setup, then restart Apex.')
        _seq += 1
        _events.append({'seq': _seq, 'kind': kind, **payload})
        del _events[:-24]
        if kind == 'activate':
            _shown = True
        elif kind == 'show':
            _shown = True
        elif kind in {'hide', 'quit'}:
            _shown = False
        _condition.notify_all()
        return _seq


def activate(item: dict) -> None:
    global _mode
    with _condition:
        _mode = {'look': 'screen', 'talk': 'voice', 'stop': 'idle'}.get(item.get('kind'), 'idle')
    publish('activate', item=item)


def show() -> None:
    if ensure():
        publish('show')


def ensure() -> bool:
    """A user hotkey can recover its opted-in window after a child crash."""
    if not running() and _url:
        start(_url)
    return running()


def acknowledge(seq: int, result: dict) -> None:
    with _condition:
        if any(e['seq'] == seq for e in _events):
            _results[seq] = {'ok': result.get('ok') is True, 'error': str(result.get('error') or '')[:300]}
            while len(_results) > 24:
                del _results[next(iter(_results))]
            _condition.notify_all()


def draw(frame: dict, shapes: list[dict], seconds: float) -> dict:
    if not status()['ready']:
        raise RuntimeError('The desktop window is not ready to draw. Open it with the hotkey first.')
    seq = publish('draw', frame=frame, shapes=shapes, seconds=seconds)
    deadline = time.monotonic() + 5
    with _condition:
        while seq not in _results:
            if not running() or time.monotonic() >= deadline:
                raise RuntimeError('The desktop overlay did not confirm drawing. No drawing is verified.')
            if any(e['seq'] > seq and e['kind'] in {'clear', 'hide', 'quit'} for e in _events):
                raise RuntimeError('Screen guidance was stopped before drawing was confirmed.')
            _condition.wait(min(.25, max(0, deadline - time.monotonic())))
        result = _results.pop(seq)
        if not result['ok']:
            raise RuntimeError(result['error'] or 'Desktop overlay could not draw.')
    return {'drawn': True, 'frame_id': frame['id'], 'shapes': len(shapes), 'expires_seconds': seconds}


def clear(hide: bool = False) -> None:
    if running():
        publish('hide' if hide else 'clear')


def hidden() -> None:
    global _shown, _mode
    with _condition:
        _shown, _mode = False, 'idle'
        clear()


def expanded() -> None:
    with _condition:
        clear()


def stop() -> None:
    global _process, _session, _ready, _shown, _url, _mode, _keys
    with _condition:
        process = _process
        if _keys:
            _keys.stop()
            _keys = None
        if running():
            publish('quit')
    if process is not None:
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            # Windows venv executables redirect to an interpreter child. Stop
            # the owned tree so a stalled window cannot survive the resident.
            from scripts.run_apex_qwen import stop as stop_process
            stop_process(process)
    with _condition:
        _process, _session, _ready, _shown = None, '', False, False
        _url, _mode = '', 'idle'
        _events.clear()
        _results.clear()
        _condition.notify_all()
