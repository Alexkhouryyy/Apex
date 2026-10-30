"""What this Apex is running right now, for the system prompt.

Celine once told the owner she had no way to do things the code plainly let her
do, because nothing in her prompt said which parts of Apex were running. This
block is built from the live state every turn and sits in the volatile tail of
the prompt beside the clock, so it never breaks prompt caching.

It must never slow a turn down: the voice check runs on a background thread and
the turn uses the last known answer, and the Companion's lock is only waited on
briefly.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

POSITIVE_CACHE_SECONDS = 30   # a ready voice is re-checked this often
NEGATIVE_CACHE_SECONDS = 5    # a missing or slow one sooner, so "ready" shows up quickly
PROBE_WAIT = 0.3              # how long a turn waits for a fresh answer before using the last one

_voice = {'at': 0.0, 'state': '', 'thread': None, 'gen': 0, 'since': 0.0, 'misses': 0, 'seen': False}
PROBE_STUCK = 3.0            # a probe running longer than this counts as no answer
_voice_lock = threading.Lock()
_BOOT = time.monotonic()
STARTING_WINDOW = 1200       # after this long, a closed port no longer means "still loading"

READY = 'ready (local)'
STARTING = "starting up: the PC's own voice speaks until she is ready"
WARMING = "warming up: the PC's own voice speaks until she is ready"
NOT_RUNNING = "not running: replies are spoken in the PC's own voice"
NOT_ANSWERING = "not answering right now: replies may use the PC's own voice"


def _key(combo) -> str:
    """'<ctrl>+<alt>+c' -> 'Ctrl+Alt+C', '<f8>' -> 'F8'; '' when none is set."""
    combo = str(combo or '')
    parts = combo.split('+')
    if combo.endswith('++'):                      # '<ctrl>++' means Ctrl and the plus key
        parts = parts[:-2] + ['+']
    out = []
    for part in parts:
        part = part.strip().strip('<>') if part != '+' else '+'
        if not part:
            continue
        out.append(part.upper() if len(part) == 1 or re.fullmatch(r'f\d{1,2}', part, re.I) else part.capitalize())
    return '+'.join(out)


def _hotkey(combo, what: str) -> str:
    key = _key(combo)
    return f'{key} {what}' if key else f'(no hotkey set) {what}'


def classify(health=None, error=None) -> str:
    """One voice state from a /health answer or the error the probe hit."""
    if error is not None:
        refused = isinstance(error, ConnectionRefusedError) or isinstance(
            getattr(error, 'reason', None), ConnectionRefusedError)
        if refused:
            # The launcher says when it started her: then a closed port means
            # "still loading", not "gone".
            starting = (os.environ.get('APEX_LOCAL_VOICE') == 'starting'
                        and time.monotonic() - _BOOT < STARTING_WINDOW
                        and not _voice.get('seen'))      # once she has answered, a closed port means gone
            return STARTING if starting else NOT_RUNNING
        return NOT_ANSWERING
    if not isinstance(health, dict) or health.get('service') not in ('apex-qwen', None):
        return NOT_RUNNING                         # some other program on the port is not Celine
    if health.get('service') is None and not health.get('model_loaded'):
        return NOT_RUNNING
    return READY if health.get('model_loaded') else WARMING


def _probe(url: str, fetch=None) -> str:
    try:
        if fetch is not None:
            return classify(fetch(url))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url.rstrip('/') + '/health', timeout=2) as response:
            return classify(json.loads(response.read(65536)))
    except Exception as exc:
        return classify(error=exc)


def _voice_state(url: str, *, now: float | None = None, fetch=None, wait: float = PROBE_WAIT) -> str:
    """The voice state, at most `wait` seconds late: a stale answer starts one
    background probe; the turn takes its result if it arrives in time, else the
    last known state. A ready voice survives one slow answer, not two. A probe
    stuck past PROBE_STUCK (a trickling server) means "not answering", and no
    second probe is started while it lives."""
    now = time.monotonic() if now is None else now
    with _voice_lock:
        state, at = _voice['state'], _voice['at']
        worker = _voice.get('thread')
        in_flight = worker is not None and worker.is_alive()
        if in_flight and now - _voice.get('since', now) > PROBE_STUCK:
            if state != NOT_ANSWERING:
                _voice.update(state=NOT_ANSWERING, at=now, misses=_voice.get('misses', 0) + 1)
            return NOT_ANSWERING
        ttl = POSITIVE_CACHE_SECONDS if state == READY else NEGATIVE_CACHE_SECONDS
        if state and now - at < ttl:
            return state
        start = not in_flight                      # never a second probe while one is out
        if start:
            _voice['since'] = now
    done = threading.Event()

    def run():
        result = _probe(url, fetch)
        with _voice_lock:
            if result in (READY, WARMING):
                _voice.update(seen=True, misses=0)
            elif result == NOT_ANSWERING and _voice['state'] == READY and not _voice.get('misses'):
                _voice['misses'] = 1
                result = READY                     # one slow answer doesn't undo a ready voice; two do
            else:
                _voice['misses'] = _voice.get('misses', 0) + 1
            _voice.update(state=result, at=now)
        done.set()
    if start:
        thread = threading.Thread(target=run, daemon=True, name='LiveStatusVoice')
        with _voice_lock:
            _voice['thread'] = thread
        thread.start()
    done.wait(wait if start else 0)
    with _voice_lock:
        return _voice['state'] or NOT_ANSWERING


def status_block(compact: bool = False) -> str:
    """The block for the prompt. compact=True is the offline (Apocalypse) form,
    for a small local model with a tight prompt budget."""
    import config
    short = []          # the one-line offline form
    lines = ['## WHAT IS RUNNING RIGHT NOW',
             'Facts about this Apex, refreshed every turn. Never tell the owner you lack a control listed here. '
             'If something is off, say it is off and how to turn it on.']
    try:
        from agent import desktop_companion
        st = desktop_companion.status_quick() if hasattr(desktop_companion, 'status_quick') else desktop_companion.status()
        short.append('Companion ' + ('starting' if st is None else 'on' if st.get('running') else 'off'))
        if st is None:
            lines.append('- Desktop Companion: starting or busy right now.')
        elif st.get('running') and st.get('hotkey_ready', True) is False:
            lines.append(f"- Desktop Companion: running ({'open' if st.get('shown') else 'hidden'}), but its "
                         'hotkeys did not start (another program may own them); use the browser Companion page.')
        elif st.get('running'):
            lines.append(
                f"- Desktop Companion: running ({'open' if st.get('shown') else 'hidden'}). "
                + _hotkey(getattr(config, 'COMPANION_HOTKEY', ''), 'opens screen help or dictation') + '; '
                + _hotkey(getattr(config, 'COMPANION_DRAW_HOTKEY', ''), 'opens a drawing surface') + '; '
                + _hotkey(getattr(config, 'COMPANION_FOLLOW_HOTKEY', ''), 'toggles the bubble following the mouse pointer')
                + f" (follows by default: {'yes' if getattr(config, 'COMPANION_FOLLOW_CURSOR', True) else 'no'}). "
                'During screen help you can point at things with screen_draw.')
        elif getattr(config, 'COMPANION_DESKTOP_ENABLED', True):
            lines.append('- Desktop Companion: enabled but not running in this Apex (the desktop window starts with '
                         'Start-Apex-All or the sign-in launcher; desktop.log says why if it should be running). '
                         'The browser Companion page still works.')
        else:
            lines.append('- Desktop Companion: off. COMPANION_DESKTOP_ENABLED=true in .env and a restart turns it on '
                         '(the browser Companion page still works).')
    except Exception:
        pass
    try:
        from agent import look_now
        live = look_now.bound()
    except Exception:
        live = set()
    keys = []
    if 'look' in live:
        keys.append(_hotkey(getattr(config, 'CELINE_HOTKEY', ''), 'has Celine look at the screen'))
    if 'talk' in live:
        keys.append(_hotkey(getattr(config, 'CELINE_TALK_HOTKEY', ''), 'turns voice mode on/off'))
    short.append('look/talk hotkeys ' + ('on' if keys else 'off'))
    if keys:
        lines.append('- ' + '; '.join(keys) + '.')
    else:
        lines.append("- Celine's look/talk hotkeys: not active in this Apex (they need the dashboard and a desktop session).")
    wake = getattr(config, 'CELINE_WAKE_ENABLED', False)
    listening = 'wake' in live
    short.append('wake phrase ' + ('on' if listening else 'off'))
    if listening:
        lines.append('- Wake phrase ("Hey Celly"): on.')
    elif wake:
        lines.append('- Wake phrase ("Hey Celly"): enabled in settings but not listening in this Apex.')
    else:
        lines.append('- Wake phrase ("Hey Celly"): off; hotkeys start requests. CELINE_WAKE_ENABLED=true turns it on.')
    try:
        from agent import handtrack
        tracker = handtrack.active_tracker()
        state = tracker.camera_state() if tracker is not None and hasattr(tracker, 'camera_state') else None
        short.append('hands ' + ('on' if state == 'watching' else 'paused' if state == 'paused'
                                 else 'stopped' if state == 'stopped'
                                 else 'no camera' if tracker is not None else 'off'))
        if state == 'watching':
            lines.append('- Hand tracking: running (camera gestures can wake or stop Apex).')
        elif state == 'paused':
            lines.append('- Hand tracking: paused while another program uses the camera.')
        elif state == 'stopped':
            lines.append('- Hand tracking: enabled but not running (the tracker stopped).')
        elif tracker is not None:
            lines.append('- Hand tracking: enabled, but the camera is not delivering frames.')
        elif getattr(config, 'HANDTRACK_ENABLED', False):
            lines.append('- Hand tracking: enabled but not running.')
        else:
            lines.append('- Hand tracking: off. HANDTRACK_ENABLED=true turns it on.')
    except Exception:
        pass
    engine = str(getattr(config, 'TTS_ENGINE', '')).lower()
    if engine == 'voicebox':
        voice = _voice_state(getattr(config, 'VOICEBOX_URL', 'http://127.0.0.1:17494'))
        short.append('Celine voice ' + voice.split(':')[0].split(' (')[0])
        lines.append("- Celine's voice: " + voice + '.')
    elif engine:
        lines.append(f'- Voice engine: {engine}.')
    try:
        from agent import awareness
        watching = awareness.running()
    except Exception:
        watching = False
    short.append('awareness ' + ('on' if watching else 'off'))
    lines.append('- Background awareness (screen/clipboard watching, proactive notes): '
                 + ('on.' if watching else 'enabled but not running in this Apex.'
                    if getattr(config, 'AWARENESS_ENABLED', True) else 'off.'))
    try:
        from agent import apocalypse
        if apocalypse.enabled():
            lines.append('- Offline (Apocalypse) mode: on. Cloud tools are unavailable; use the local library.')
    except Exception:
        pass
    if compact:
        # Offline prompt budget (a small local model with a tight prompt): one line of facts.
        return 'Running now: ' + '; '.join(short) + '. Never deny a control that is on.'
    return '\n'.join(lines)
