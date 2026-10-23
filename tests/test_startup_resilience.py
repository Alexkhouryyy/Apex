"""One failure must not stop too much: speech without Celine's server, and
hands without the awareness monitor."""
from types import SimpleNamespace

import pytest

import config
from voice import tts


@pytest.fixture
def quiet_tts(monkeypatch):
    spoken = []
    monkeypatch.setattr(config, 'TTS_ENGINE', 'voicebox')
    monkeypatch.setattr(tts.interrupt_mod, 'start_listening_for_interrupt', lambda **k: (lambda: None))
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    monkeypatch.setattr(tts, '_speak_pyttsx3', lambda text: spoken.append(('system', text)))
    monkeypatch.setattr(tts, '_fallback_noted', False)
    return spoken


def test_speech_falls_back_to_the_pc_voice_when_celine_is_down(quiet_tts, monkeypatch, capsys):
    def down(text):
        raise ConnectionRefusedError('[WinError 10061] No connection could be made')
    monkeypatch.setattr(tts, '_speak_voicebox', down)
    assert tts.speak('The build passed.') is True
    assert tts.speak('And the tests too.') is True
    assert quiet_tts == [('system', 'The build passed.'), ('system', 'And the tests too.')]
    out = capsys.readouterr().out
    assert out.count("Celine's voice is unavailable") == 1, 'said once, not on every reply'


def test_celine_is_used_again_once_she_answers(quiet_tts, monkeypatch, capsys):
    state = {'up': False}
    said = []
    def voicebox(text):
        if not state['up']:
            raise TimeoutError('warming up')
        said.append(('celine', text))
    monkeypatch.setattr(tts, '_speak_voicebox', voicebox)
    tts.speak('one')
    state['up'] = True
    tts.speak('two')
    assert quiet_tts == [('system', 'one')] and said == [('celine', 'two')]
    state['up'] = False
    tts.speak('three')
    assert capsys.readouterr().out.count('unavailable') == 2, 'a new outage is reported again'


def test_hands_start_without_the_awareness_monitor(monkeypatch):
    from agent import handtrack
    started = []

    class Tracker:
        def __init__(self, log):
            self.log, self.on_gesture = log, None
        def start(self):
            started.append(self)
        def latest_frame(self):
            return None
    monkeypatch.setattr(handtrack, 'HandTracker', Tracker)
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', True)
    monkeypatch.setattr(handtrack, '_active', None)
    tracker = handtrack.start_standalone(start=False)
    assert tracker is not None and handtrack.active_tracker() is tracker and not started, \
        'built first, so the gesture handler is wired before any frame is read'
    handtrack.start_tracker(tracker)
    assert started == [tracker]
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', False)
    assert handtrack.start_standalone() is None, 'still off when HANDTRACK_ENABLED is off'


def test_the_startup_paths_use_standalone_hands_when_awareness_is_off():
    """Both startup paths must reach the standalone tracker, not only warn."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main_src = (root / 'main.py').read_text(encoding='utf-8')
    resident_src = (root / 'app' / 'resident.py').read_text(encoding='utf-8')
    assert 'start_standalone(start=False)' in main_src and 'start_tracker(_tracker)' in main_src
    assert 'start_standalone(start=False)' in resident_src and 'start_tracker(src)' in resident_src
    assert 'is AWARENESS_ENABLED off?' not in resident_src


# ---------------------------------------------------------------- Celine knows what is running

def test_the_prompt_says_which_controls_exist_right_now(monkeypatch):
    from agent import live_status, desktop_companion, handtrack
    monkeypatch.setattr(desktop_companion, 'status', lambda: {'running': True, 'shown': False})
    monkeypatch.setattr(handtrack, 'active_tracker', lambda: object())
    monkeypatch.setattr(config, 'TTS_ENGINE', 'voicebox')
    monkeypatch.setattr(live_status, '_voice_state', lambda url, **k: 'ready (local)')
    block = live_status.status_block()
    assert 'Never tell the owner you lack a control listed here' in block
    assert 'Desktop Companion: running (hidden). F8' in block and 'Ctrl+Alt+F toggles the bubble' in block
    assert 'screen_draw' in block and 'Hand tracking: running' in block and "Celine's voice: ready (local)" in block


def test_what_is_off_says_how_to_turn_it_on(monkeypatch):
    from agent import live_status, desktop_companion, handtrack
    monkeypatch.setattr(desktop_companion, 'status', lambda: {'running': False})
    monkeypatch.setattr(handtrack, 'active_tracker', lambda: None)
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', False)
    monkeypatch.setattr(config, 'CELINE_WAKE_ENABLED', False)
    block = live_status.status_block()
    assert 'COMPANION_DESKTOP_ENABLED=true' in block and 'HANDTRACK_ENABLED=true' in block
    assert 'CELINE_WAKE_ENABLED=true turns it on' in block


def test_the_voice_check_is_cached_and_reports_warm_up(monkeypatch):
    from agent import live_status
    monkeypatch.setattr(live_status, '_voice_cache', {'at': 0.0, 'state': ''})
    calls = []
    def fetch(url):
        calls.append(url)
        return {'service': 'apex-qwen', 'model_loaded': False}
    assert 'warming up' in live_status._voice_state('http://127.0.0.1:17494', now=100.0, fetch=fetch)
    live_status._voice_state('http://127.0.0.1:17494', now=110.0, fetch=fetch)
    assert len(calls) == 1, 'one local check per 30 s, not one per turn'
    def down(url):
        raise ConnectionRefusedError
    assert 'not running' in live_status._voice_state('x', now=200.0, fetch=down)


def test_a_broken_status_never_breaks_a_turn(monkeypatch):
    from agent import core, live_status
    monkeypatch.setattr(live_status, 'status_block', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert core._live_status() == ''
