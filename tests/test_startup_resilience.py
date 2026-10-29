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
    monkeypatch.setattr(tts, '_speak_system', lambda text: spoken.append(('system', text)))
    monkeypatch.setattr(tts, '_play', lambda audio: spoken.append(('celine', audio)))
    monkeypatch.setattr(tts, '_fallback_noted', False)
    monkeypatch.setattr(tts, '_celine_down_until', 0.0)
    return spoken


def down(text):
    raise ConnectionRefusedError('[WinError 10061] No connection could be made')


def test_speech_falls_back_to_the_pc_voice_when_celine_is_down(quiet_tts, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tts, '_synthesize', lambda text: calls.append(text) or down(text))
    assert tts.speak('The build passed.') is True
    assert tts.speak('And the tests too.') is True
    assert quiet_tts == [('system', 'The build passed.'), ('system', 'And the tests too.')]
    assert calls == ['The build passed.'], 'cool-down: the second sentence does not wait for the same failure'
    assert capsys.readouterr().out.count("Celine's voice is unavailable") == 1, 'said once per outage'


def test_celine_is_used_again_after_the_cool_down(quiet_tts, monkeypatch, capsys):
    up = {'v': False}
    monkeypatch.setattr(tts, '_synthesize', lambda text: (b'wav:' + text.encode()) if up['v'] else down(text))
    tts.speak('one')
    up['v'] = True
    tts.speak('two')                                   # still inside the cool-down
    monkeypatch.setattr(tts, '_celine_down_until', 0.0)  # cool-down over
    tts.speak('three')
    assert quiet_tts == [('system', 'one'), ('system', 'two'), ('celine', b'wav:three')]
    up['v'] = False
    tts.speak('four')
    assert capsys.readouterr().out.count('unavailable') == 2, 'a new outage is reported again'


def test_a_sentence_is_never_said_twice_when_playback_breaks_part_way(quiet_tts, monkeypatch, capsys):
    monkeypatch.setattr(tts, '_synthesize', lambda text: b'wav')
    def broken(audio):
        raise tts.PlaybackFailed('PortAudioError: device lost', started=True)
    monkeypatch.setattr(tts, '_play', broken)
    tts.speak('Half of this was heard.')
    assert quiet_tts == [], 'no PC-voice repeat of a sentence the owner already half heard'
    assert 'not repeating the sentence' in capsys.readouterr().out
    def never_started(audio):
        raise tts.PlaybackFailed('PortAudioError: no device', started=False)
    monkeypatch.setattr(tts, '_play', never_started)
    tts.speak('Nothing of this was heard.')
    assert quiet_tts == [('system', 'Nothing of this was heard.')]


def test_long_replies_are_split_so_celine_can_read_them(quiet_tts, monkeypatch):
    seen = []
    monkeypatch.setattr(tts, '_synthesize', lambda text: seen.append(text) or b'wav')
    text = ('This sentence is about forty characters. ' * 250).strip()      # ~10,000 characters
    tts.speak(text)
    assert len(seen) == 3 and all(len(piece) <= tts.MAX_CHUNK for piece in seen)
    assert ' '.join(seen) == text and all(p.endswith('.') for p in seen)
    assert [kind for kind, _ in quiet_tts] == ['celine'] * 3


def test_a_busy_celine_is_waited_for_briefly_then_the_pc_voice_speaks(monkeypatch):
    import asyncio
    attempts = []
    async def synthesize(text):
        attempts.append(text)
        if len(attempts) < 3:
            raise ValueError('Voicebox is already generating an Apex reply. Try again when it finishes.')
        return b'wav'
    import voice.voicebox as vb
    monkeypatch.setattr(vb, 'synthesize', synthesize)
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    monkeypatch.setattr(__import__('time'), 'sleep', lambda s: None)
    assert tts._synthesize('hi') == b'wav' and len(attempts) == 3


def test_a_hung_voice_server_gives_up_within_the_budget_and_on_interrupt(monkeypatch):
    import asyncio
    import time as _time
    async def hang(text):
        await asyncio.sleep(3600)
    import voice.voicebox as vb
    monkeypatch.setattr(vb, 'synthesize', hang)
    monkeypatch.setattr(tts, 'SYNTH_BUDGET', 0.5)
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    started = _time.monotonic()
    with pytest.raises(TimeoutError):
        tts._synthesize('hi')
    assert _time.monotonic() - started < 2
    monkeypatch.setattr(tts, 'SYNTH_BUDGET', 60)
    flag = {'n': 0}
    def interrupted():
        flag['n'] += 1
        return flag['n'] > 2
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', interrupted)
    started = _time.monotonic()
    with pytest.raises(InterruptedError):
        tts._synthesize('hi')
    assert _time.monotonic() - started < 2, 'barge-in frees a hung synthesis'


def test_the_pc_voice_stops_on_barge_in_and_never_raises(monkeypatch, capsys):
    import time as _time
    stopped = []

    class Engine:
        def say(self, text):
            self.text = text
        def runAndWait(self):
            for _ in range(100):                    # "speaking" for up to 5 s
                if stopped:
                    return
                _time.sleep(0.05)
        def stop(self):
            stopped.append(True)
    monkeypatch.setattr(tts, '_get_pyttsx3', lambda: Engine())
    flag = {'at': _time.monotonic() + 0.2}
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: _time.monotonic() > flag['at'])
    begun = _time.monotonic()
    tts._speak_system('A long fallback sentence.')
    assert stopped and _time.monotonic() - begun < 2, 'the stop gesture / barge-in reaches the PC voice'

    def broken():
        raise RuntimeError('SAPI5 not available')
    monkeypatch.setattr(tts, '_get_pyttsx3', broken)
    tts._speak_system('Nobody can say this.')      # must not raise into the streamer / resident loop
    out = capsys.readouterr().out
    assert "Neither Celine's voice nor this PC's voice could speak" in out and 'SAPI5' in out


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
    monkeypatch.setattr(desktop_companion, 'status_quick', lambda wait=0.1: {'running': True, 'shown': False})
    tracker = SimpleNamespace(camera_state=lambda: 'watching')
    monkeypatch.setattr(handtrack, 'active_tracker', lambda: tracker)
    monkeypatch.setattr(config, 'TTS_ENGINE', 'voicebox')
    monkeypatch.setattr(live_status, '_voice_state', lambda url, **k: live_status.READY)
    block = live_status.status_block()
    assert 'Never tell the owner you lack a control listed here' in block
    assert 'Desktop Companion: running (hidden). F8' in block and 'Ctrl+Alt+F toggles the bubble' in block
    assert 'screen_draw' in block and 'Hand tracking: running' in block and "Celine's voice: ready (local)" in block


@pytest.mark.parametrize('state, says', [('no_camera', 'camera is not delivering frames'),
                                          ('paused', 'paused while another program uses the camera'),
                                          ('stopped', 'camera is not delivering frames')])
def test_hand_tracking_is_only_called_running_when_frames_arrive(monkeypatch, state, says):
    from agent import live_status, handtrack
    monkeypatch.setattr(handtrack, 'active_tracker', lambda: SimpleNamespace(camera_state=lambda: state))
    block = live_status.status_block()
    assert says in block and 'Hand tracking: running' not in block


def test_what_is_off_says_how_to_turn_it_on(monkeypatch):
    from agent import live_status, desktop_companion, handtrack
    monkeypatch.setattr(desktop_companion, 'status_quick', lambda wait=0.1: {'running': False})
    monkeypatch.setattr(handtrack, 'active_tracker', lambda: None)
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', False)
    monkeypatch.setattr(config, 'CELINE_WAKE_ENABLED', False)
    block = live_status.status_block()
    assert 'COMPANION_DESKTOP_ENABLED=true' in block and 'HANDTRACK_ENABLED=true' in block
    assert 'CELINE_WAKE_ENABLED=true turns it on' in block


def test_a_busy_companion_never_makes_the_turn_wait(monkeypatch):
    import threading
    import time as _time
    from agent import desktop_companion, live_status
    holder = threading.Event()
    release = threading.Event()
    def hold():
        with desktop_companion._condition:
            holder.set()
            release.wait(5)
    threading.Thread(target=hold, daemon=True).start()
    holder.wait(2)
    try:
        started = _time.monotonic()
        block = live_status.status_block()
        assert _time.monotonic() - started < 1.5 and 'Desktop Companion: starting or busy' in block
    finally:
        release.set()


@pytest.mark.parametrize('health, error, env, expected', [
    ({'service': 'apex-qwen', 'model_loaded': True}, None, '', 'READY'),
    ({'service': 'apex-qwen', 'model_loaded': False}, None, '', 'WARMING'),
    ({'service': 'something-else', 'model_loaded': False}, None, '', 'NOT_RUNNING'),
    ({'status': 'ok'}, None, '', 'NOT_RUNNING'),
    (None, ConnectionRefusedError(), '', 'NOT_RUNNING'),
    (None, ConnectionRefusedError(), 'starting', 'STARTING'),
    (None, TimeoutError('timed out'), '', 'NOT_ANSWERING'),
])
def test_voice_states_are_told_apart(monkeypatch, health, error, env, expected):
    from agent import live_status
    monkeypatch.setenv('APEX_LOCAL_VOICE', env)
    assert live_status.classify(health, error) == getattr(live_status, expected)


def test_the_voice_check_is_cached_and_never_stalls_a_turn(monkeypatch):
    import time as _time
    from agent import live_status
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': '', 'busy': False})
    calls = []
    def fetch(url):
        calls.append(url)
        return {'service': 'apex-qwen', 'model_loaded': True}
    assert live_status._voice_state('u', now=100.0, fetch=fetch) == live_status.READY
    live_status._voice_state('u', now=120.0, fetch=fetch)
    assert len(calls) == 1, 'a ready voice is re-checked every 30 s, not every turn'
    def trickle(url):
        _time.sleep(3)                      # a server that answers one byte at a time
        return {}
    started = _time.monotonic()
    state = live_status._voice_state('u', now=200.0, fetch=trickle)
    assert _time.monotonic() - started < 1 and state == live_status.READY, \
        'the turn moves on with the last known state while the probe is still running'


def test_a_slow_answer_does_not_undo_a_ready_voice(monkeypatch):
    from agent import live_status
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': live_status.READY, 'busy': False})
    def slow(url):
        raise TimeoutError('timed out')
    assert live_status._voice_state('u', now=500.0, fetch=slow) == live_status.READY


def test_the_offline_prompt_gets_one_short_line(monkeypatch):
    from agent import live_status
    line = live_status.status_block(compact=True)
    assert line.startswith('Running now:') and '\n' not in line and len(line) < 200


def test_a_broken_status_never_breaks_a_turn(monkeypatch):
    from agent import core, live_status
    monkeypatch.setattr(live_status, 'status_block', lambda compact=False: (_ for _ in ()).throw(RuntimeError('boom')))
    assert core._live_status() == ''


def test_the_hand_tracker_thread_can_be_inspected_and_joined():
    """self._stop shadowed threading.Thread._stop, so is_alive() and join() raised."""
    from agent import handtrack
    tracker = handtrack.HandTracker(SimpleNamespace(add=lambda *a: None))
    assert tracker.is_alive() is False and tracker.camera_state() == 'no_camera'
    tracker._stop_event.set()            # stop before the first camera attempt
    tracker.start()
    tracker.join(timeout=5)
    assert tracker.is_alive() is False and tracker.camera_state() == 'stopped'


def test_a_pc_voice_failing_mid_sentence_is_logged_not_raised(monkeypatch, capsys):
    class Engine:
        def say(self, text):
            pass
        def runAndWait(self):
            raise OSError('audio device disappeared')
        def stop(self):
            pass
    monkeypatch.setattr(tts, '_get_pyttsx3', lambda: Engine())
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    tts._speak_system('Still here.')
    assert 'audio device disappeared' in capsys.readouterr().out


# ---------------------------------------------------------------- hands: what the stress test found

def test_the_camera_is_released_while_the_hand_model_downloads(monkeypatch, tmp_path, capsys):
    """On a slow line the webcam was held for the whole model download."""
    import threading
    from agent import handtrack
    monkeypatch.setattr(handtrack, 'model_path', lambda: tmp_path / 'hand.task')
    gate = threading.Event()
    monkeypatch.setattr(handtrack, 'ensure_model', lambda timeout=120.0: gate.wait(5))
    monkeypatch.setattr(handtrack, '_download', {'thread': None})
    released = []

    class Camera:
        def isOpened(self):
            return True
        def release(self):
            released.append(True)
    monkeypatch.setattr('cv2.VideoCapture', lambda *a: Camera())
    import sys, types
    fake_mp = types.ModuleType('mediapipe')
    monkeypatch.setitem(sys.modules, 'mediapipe', fake_mp)
    fake_tasks = types.ModuleType('mediapipe.tasks'); fake_python = types.ModuleType('mediapipe.tasks.python')
    fake_python.BaseOptions = object; fake_python.vision = object
    monkeypatch.setitem(sys.modules, 'mediapipe.tasks', fake_tasks)
    monkeypatch.setitem(sys.modules, 'mediapipe.tasks.python', fake_python)
    t = handtrack.HandTracker(SimpleNamespace(add=lambda *a: None))
    assert t._open(now=100.0) is False
    assert released and t._cap is None, 'camera handed back during the download'
    assert t._retry_at == pytest.approx(103.0)
    assert 'camera stays free until it is ready' in capsys.readouterr().out
    assert handtrack._download['thread'] is not None, 'the download runs in the background'
    gate.set()


def test_starting_hands_twice_keeps_one_tracker(monkeypatch):
    from agent import handtrack
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', True)
    running = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(handtrack, '_active', running)
    assert handtrack.start_standalone() is running, 'a second tracker would fight the first for the webcam'


def test_boot_never_says_hands_are_off_when_they_start_on_their_own(monkeypatch):
    from agent import awareness
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', True)
    line = awareness.report_hand_tracking(None)
    assert 'OFF' not in line and 'starts on its own' in line


def test_a_failed_awareness_start_falls_back_to_standalone_hands():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'app' / 'resident.py').read_text(encoding='utf-8')
    failed = src.index('Awareness monitor failed to start in resident mode')
    assert 'awareness_monitor = None' in src[failed:failed + 400]
