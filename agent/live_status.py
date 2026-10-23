"""What this Apex is running right now, for the system prompt.

Celine once told the owner she had no way to do things the code plainly let her
do, because nothing in her prompt said which parts of Apex were running. This
block is built from the live state every turn (cheap: in-process flags, plus one
cached local health check for the voice server) and sits in the volatile tail of
the prompt beside the clock, so it never breaks prompt caching.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

_voice_cache: dict = {'at': 0.0, 'state': ''}
VOICE_CACHE_SECONDS = 30


def _key(combo: str) -> str:
    """'<ctrl>+<alt>+c' -> 'Ctrl+Alt+C', '<f8>' -> 'F8'."""
    out = []
    for part in str(combo or '').split('+'):
        part = part.strip().strip('<>')
        if not part:
            continue
        out.append(part.upper() if len(part) == 1 or re.fullmatch(r'f\d{1,2}', part) else part.capitalize())
    return '+'.join(out) or 'none'


def _voice_state(url: str, *, now: float | None = None, fetch=None) -> str:
    now = time.monotonic() if now is None else now
    if now - _voice_cache['at'] < VOICE_CACHE_SECONDS and _voice_cache['state']:
        return _voice_cache['state']
    state = 'not running: replies are spoken in the PC\'s own voice'
    try:
        if fetch is not None:
            health = fetch(url)
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url.rstrip('/') + '/health', timeout=0.3) as response:
                health = json.load(response)
        if isinstance(health, dict) and health.get('model_loaded'):
            state = 'ready (local)'
        elif isinstance(health, dict) and health:
            state = 'warming up: the PC\'s own voice speaks until it is ready'
    except Exception:
        pass
    _voice_cache.update(at=now, state=state)
    return state


def status_block() -> str:
    import config
    lines = ['## WHAT IS RUNNING RIGHT NOW',
             'Facts about this Apex, refreshed every turn. Never tell the owner you lack a control listed here. '
             'If something is off, say it is off and how to turn it on.']
    try:
        from agent import desktop_companion
        st = desktop_companion.status()
        if st.get('running'):
            lines.append(f"- Desktop Companion: running ({'open' if st.get('shown') else 'hidden'}). "
                         f"{_key(config.COMPANION_HOTKEY)} opens screen help or dictation; "
                         f"{_key(config.COMPANION_DRAW_HOTKEY)} opens a drawing surface; "
                         f"{_key(config.COMPANION_FOLLOW_HOTKEY)} toggles the bubble following the mouse pointer "
                         f"(follows by default: {'yes' if config.COMPANION_FOLLOW_CURSOR else 'no'}). "
                         'During screen help you can point at things with screen_draw.')
        else:
            lines.append('- Desktop Companion: not running. COMPANION_DESKTOP_ENABLED=true in .env and a restart turns it on '
                         '(the browser Companion page still works).')
    except Exception:
        pass
    lines.append(f"- Look at the screen: {_key(config.CELINE_HOTKEY)}. Voice mode on/off: {_key(config.CELINE_TALK_HOTKEY)}.")
    wake = getattr(config, 'CELINE_WAKE_ENABLED', False)
    lines.append('- Wake phrase ("Hey Celly"): ' + ('on.' if wake else 'off; hotkeys start requests. CELINE_WAKE_ENABLED=true turns it on.'))
    try:
        from agent import handtrack
        tracker = handtrack.active_tracker()
        if tracker is not None:
            lines.append('- Hand tracking: running (camera gestures can wake or stop Apex).')
        elif getattr(config, 'HANDTRACK_ENABLED', False):
            lines.append('- Hand tracking: enabled but not running (camera unavailable or not started).')
        else:
            lines.append('- Hand tracking: off. HANDTRACK_ENABLED=true turns it on.')
    except Exception:
        pass
    if str(getattr(config, 'TTS_ENGINE', '')).lower() == 'voicebox':
        lines.append('- Celine\'s voice: ' + _voice_state(getattr(config, 'VOICEBOX_URL', 'http://127.0.0.1:17494')) + '.')
    else:
        lines.append(f"- Voice engine: {config.TTS_ENGINE}.")
    lines.append('- Background awareness (screen/clipboard watching, proactive notes): '
                 + ('on.' if getattr(config, 'AWARENESS_ENABLED', True) else 'off.'))
    try:
        from agent import apocalypse
        if apocalypse.enabled():
            lines.append('- Offline (Apocalypse) mode: on. Cloud tools are unavailable; use the local library.')
    except Exception:
        pass
    return '\n'.join(lines)
