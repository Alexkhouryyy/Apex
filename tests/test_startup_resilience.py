"""One failure must not stop too much: speech without Celine's server, and
hands without the awareness monitor."""
from types import SimpleNamespace

import pytest

import config
from voice import tts

REAL_PLAY = tts._play


@pytest.fixture
def quiet_tts(monkeypatch):
    spoken = []
    monkeypatch.setattr(config, 'TTS_ENGINE', 'voicebox')
    monkeypatch.setattr(tts.interrupt_mod, 'start_listening_for_interrupt', lambda **k: (lambda: None))
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    monkeypatch.setattr(tts, '_speak_system', lambda text: spoken.append(('system', text)))
    monkeypatch.setattr(tts, '_play', lambda audio: spoken.append(('celine', audio)))
    monkeypatch.setattr(tts, '_fallback_noted', False)
    monkeypatch.setattr(tts, '_noted_at', 0.0)
    monkeypatch.setattr(tts, '_celine_down_until', 0.0)
    monkeypatch.setattr(tts, '_celine_busy_until', 0.0)
    monkeypatch.setattr(tts, '_audio_missing', '')
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
    assert capsys.readouterr().out.count('unavailable') == 1, 'a voice flapping within minutes is not re-announced'
    monkeypatch.setattr(tts, '_noted_at', tts._noted_at - tts.NOTICE_GAP)   # minutes later
    monkeypatch.setattr(tts, '_celine_down_until', 0.0)
    up['v'] = True
    tts.speak('five')
    up['v'] = False
    monkeypatch.setattr(tts, '_celine_down_until', 0.0)
    tts.speak('six')
    assert capsys.readouterr().out.count('unavailable') == 1, 'a new outage after a while is reported again'


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
                                          ('stopped', 'enabled but not running (the tracker stopped)')])
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
    monkeypatch.setattr(config, 'COMPANION_DESKTOP_ENABLED', False)
    from agent import look_now
    monkeypatch.setattr(look_now, '_bound', set())     # other tests may have bound listeners
    block = live_status.status_block()
    assert 'COMPANION_DESKTOP_ENABLED=true' in block and 'HANDTRACK_ENABLED=true' in block
    assert 'CELINE_WAKE_ENABLED=true turns it on' in block


def test_only_what_actually_started_is_listed(monkeypatch):
    """The Apocalypse child and a dashboard-off Apex never bind Celine's hotkeys
    or start awareness; the prompt used to list them anyway."""
    from agent import live_status, desktop_companion, look_now, awareness
    monkeypatch.setattr(desktop_companion, 'status_quick', lambda wait=0.1: {'running': False})
    monkeypatch.setattr(config, 'COMPANION_DESKTOP_ENABLED', True)
    monkeypatch.setattr(config, 'CELINE_WAKE_ENABLED', True)
    monkeypatch.setattr(config, 'AWARENESS_ENABLED', True)
    monkeypatch.setattr(look_now, '_bound', set())
    monkeypatch.setattr(awareness, '_running', False)
    block = live_status.status_block()
    assert 'Ctrl+Alt+C' not in block and 'hotkeys: not active in this Apex' in block
    assert 'enabled in settings but not listening' in block
    assert 'Background awareness (screen/clipboard watching, proactive notes): enabled but not running' in block
    assert 'enabled but not running in this Apex' in block and 'a restart turns it on' not in block, \
        'no "set it and restart" advice when it is already set'
    look_now.mark_bound('look', 'talk', 'wake')
    monkeypatch.setattr(awareness, '_running', True)
    block = live_status.status_block()
    assert 'Ctrl+Alt+C has Celine look at the screen' in block and 'Wake phrase ("Hey Celly"): on.' in block
    assert 'proactive notes): on.' in block


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
    tracker.join(timeout=60)              # a cold MediaPipe import can take a while before the stop is seen
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


# ---------------------------------------------------------------- listeners record what really started

def test_resident_records_the_hotkeys_and_wake_phrase_it_binds(monkeypatch):
    from unittest.mock import Mock
    from app import resident
    from app.hotkey import GlobalHotkeys
    from agent import look_now
    from voice import wake
    for key, value in {'DASHBOARD_ENABLED': True, 'WAKE_WORD_ENABLED': False,
                       'CELINE_WAKE_ENABLED': True, 'CELINE_WAKE_PHRASES': ['hey celly']}.items():
        monkeypatch.setattr(resident.config, key, value)
    alive = SimpleNamespace(_thread=SimpleNamespace(is_alive=lambda: True), start=lambda on_wake: None)
    monkeypatch.setattr(wake, 'WakeWordListener', Mock(return_value=alive))
    monkeypatch.setattr(look_now, '_bound', set())
    resident._configure_attention(GlobalHotkeys(), Mock(), lambda: False)
    resident._record_celine_hotkeys(False)          # the hotkey listener failed to start
    assert look_now.bound() == {'wake'}, 'hotkeys that never started are not listed'
    resident._record_celine_hotkeys(True)
    assert look_now.bound() == {'look', 'talk', 'wake'}


def test_a_dead_wake_listener_is_not_reported_as_listening(monkeypatch):
    from agent import look_now
    monkeypatch.setattr(look_now, '_bound', set())
    thread = SimpleNamespace(alive=True, is_alive=lambda: thread.alive)
    look_now.mark_bound('wake', listener=SimpleNamespace(_thread=thread))
    assert 'wake' in look_now.bound()
    thread.alive = False                             # no microphone: the WakeWord thread died
    assert 'wake' not in look_now.bound()


def test_a_failed_awareness_start_cleans_up_and_is_not_running(monkeypatch):
    from agent import awareness
    stopped = []
    monitor = object.__new__(awareness.AwarenessMonitor)
    def boom():
        raise OSError('clipboard unavailable')
    monitor.window = SimpleNamespace(start=lambda: None, stop=lambda: stopped.append('window'))
    monitor.clipboard = SimpleNamespace(start=boom, stop=lambda: stopped.append('clipboard'))
    monitor.files = SimpleNamespace(start=lambda: None, stop=lambda: stopped.append('files'))
    monitor.iot = monitor.handtrack = None
    monitor._stop = __import__('threading').Event()
    monkeypatch.setattr(awareness, '_running', False)
    with pytest.raises(OSError):
        monitor.start()
    assert awareness.running() is False and 'window' in stopped, 'the watcher that did start was stopped again'


def test_look_now_start_records_what_it_bound(monkeypatch):
    from agent import look_now
    import app.hotkey as hk

    class Keys:
        def __init__(self): self.b = {}
        def bind(self, combo, fn): self.b[combo] = fn
        def start(self): return True
    monkeypatch.setattr(hk, 'GlobalHotkeys', Keys)
    monkeypatch.setattr(look_now, '_bound', set())
    look_now.start('<ctrl>+<alt>+c', None, '<ctrl>+<alt>+t')
    assert look_now.bound() == {'look', 'talk'}


def test_awareness_reports_running_only_between_start_and_stop(monkeypatch):
    from agent import awareness
    monitor = object.__new__(awareness.AwarenessMonitor)
    stub = SimpleNamespace(start=lambda: None, stop=lambda: None)
    monitor.window = monitor.clipboard = monitor.files = stub
    monitor.iot = monitor.handtrack = None
    monitor.review_interval = 60
    monitor._stop = __import__('threading').Event()
    monkeypatch.setattr(awareness.AwarenessMonitor, '_review_loop', lambda self: None)
    monkeypatch.setattr(awareness, '_running', False)
    monitor.start()
    assert awareness.running() is True
    monitor.stop()
    assert awareness.running() is False


# ---------------------------------------------------------------- round 2 of the stress test

def test_a_ready_voice_that_stops_answering_is_not_called_ready_forever(monkeypatch):
    from agent import live_status
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': live_status.READY, 'busy': False,
                                                'since': 0.0, 'misses': 0, 'seen': True})
    def slow(url):
        raise TimeoutError('timed out')
    assert live_status._voice_state('u', now=100.0, fetch=slow) == live_status.READY, 'one slow answer is forgiven'
    assert live_status._voice_state('u', now=200.0, fetch=slow) == live_status.NOT_ANSWERING, 'two are not'


def test_a_stuck_probe_stops_the_old_state_being_trusted(monkeypatch):
    from agent import live_status
    stuck = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': live_status.READY, 'thread': stuck, 'gen': 1,
                                                'since': 100.0, 'misses': 0, 'seen': True})
    assert live_status._voice_state('u', now=110.0) == live_status.NOT_ANSWERING
    assert live_status._voice['thread'] is stuck, 'no second probe while the stuck one lives'


def test_stuck_voice_probes_never_pile_up(monkeypatch):
    import threading
    import time as _time
    from agent import live_status
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': '', 'thread': None, 'gen': 0,
                                                'since': 0.0, 'misses': 0, 'seen': False})
    gate = threading.Event()
    calls = []
    def trickle(url):
        calls.append(1)
        gate.wait(10)
        return {'service': 'apex-qwen', 'model_loaded': True}
    for i in range(30):
        live_status._voice_state('u', now=100.0 + i * 10, fetch=trickle, wait=0)
    assert len(calls) == 1, 'one stuck probe, not one per turn'
    gate.set()
    _time.sleep(0.2)
    # Within the first seconds, before it counts as stuck, turns must not start more probes either.
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': '', 'thread': None, 'gen': 0,
                                                'since': 0.0, 'misses': 0, 'seen': False})
    gate.clear()
    calls.clear()
    for i in range(10):
        live_status._voice_state('u', now=500.0 + i * 0.1, fetch=trickle, wait=0)
    assert len(calls) == 1, 'a probe still out (not yet stuck) is not joined by another'
    gate.set()
    _time.sleep(0.2)


def test_a_voice_that_crashed_after_answering_is_not_called_starting(monkeypatch):
    from agent import live_status
    monkeypatch.setenv('APEX_LOCAL_VOICE', 'starting')
    monkeypatch.setattr(live_status, '_voice', {'at': 0.0, 'state': '', 'thread': None, 'gen': 0, 'since': 0.0,
                                                'misses': 0, 'seen': False})
    assert live_status.classify(error=ConnectionRefusedError()) == live_status.STARTING
    live_status._voice['seen'] = True
    assert live_status.classify(error=ConnectionRefusedError()) == live_status.NOT_RUNNING


def test_companion_hotkeys_are_not_listed_when_they_did_not_start(monkeypatch):
    from agent import live_status, desktop_companion
    monkeypatch.setattr(desktop_companion, 'status_quick',
                        lambda wait=0.1: {'running': True, 'shown': False, 'hotkey_ready': False})
    block = live_status.status_block()
    assert 'hotkeys did not start' in block and 'F8 opens' not in block


def test_the_offline_line_covers_hotkeys_and_awareness():
    from agent import live_status
    line = live_status.status_block(compact=True)
    assert 'look/talk hotkeys' in line and 'awareness' in line


def test_audio_that_cannot_even_load_falls_back_instead_of_raising(quiet_tts, monkeypatch, capsys):
    """PortAudio missing: speak() raised and killed the streaming voice worker."""
    import builtins
    real = builtins.__import__
    def no_audio(name, *a, **k):
        if name == 'sounddevice':
            raise OSError('PortAudio library not found')
        return real(name, *a, **k)
    monkeypatch.setattr(tts, '_play', REAL_PLAY)
    monkeypatch.setattr(builtins, '__import__', no_audio)
    monkeypatch.setattr(tts, '_synthesize', lambda text: b'RIFF')
    assert tts.speak('Hello there.') is True
    assert ('system', 'Hello there.') in quiet_tts
    assert tts._celine_down_until > 0, 'nothing was heard, so the cool-down starts'


def test_a_busy_celine_is_not_an_outage(quiet_tts, monkeypatch, capsys):
    calls = []
    def busy(text):
        calls.append(text)
        raise tts.CelineBusy('Voicebox 409: The voice is generating another section')
    monkeypatch.setattr(tts, '_synthesize', busy)
    tts.speak('One.')
    tts.speak('Two.')
    assert quiet_tts == [('system', 'One.'), ('system', 'Two.')]
    assert calls == ['One.', 'Two.'], 'no cool-down: she is asked again for the next sentence'
    assert tts._celine_down_until == 0.0 and 'unavailable' not in capsys.readouterr().out


def test_the_server_409_counts_as_busy():
    assert tts._is_busy(ValueError('Voicebox 409: The voice is generating another section; try again shortly'))
    assert tts._is_busy(ValueError('Voicebox is already generating an Apex reply. Try again when it finishes.'))
    assert not tts._is_busy(ValueError('Voicebox 500: model crashed'))


def test_speech_works_from_inside_a_running_event_loop(monkeypatch):
    import asyncio
    async def synth(text):
        return b'wav'
    import voice.voicebox as vb
    monkeypatch.setattr(vb, 'synthesize', synth)
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    async def inside():
        return tts._synthesize('hi')
    assert asyncio.run(inside()) == b'wav'


def test_long_text_without_sentence_ends_is_split_between_words():
    text = ('words ' * 2000).strip()                  # 4000 is not a multiple of 6: a hard cut would split a word
    pieces = tts._chunks(text)
    assert all(len(p) <= tts.MAX_CHUNK for p in pieces) and all(p.startswith('words') and p.endswith('words') for p in pieces)
    assert ' '.join(pieces) == text



# ---------------------------------------------------------------- round 3 of the stress test

def test_the_streaming_409_counts_as_busy():
    assert tts._is_busy(ValueError('Voicebox could not generate audio (409): The voice is generating another section'))


def test_a_long_busy_spell_does_not_cost_four_seconds_a_sentence(monkeypatch):
    import time as _time
    async def busy(text):
        raise ValueError('Voicebox is already generating an Apex reply. Try again when it finishes.')
    import voice.voicebox as vb
    monkeypatch.setattr(vb, 'synthesize', busy)
    monkeypatch.setattr(tts.interrupt_mod, 'is_interrupted', lambda: False)
    monkeypatch.setattr(tts, '_celine_busy_until', _time.monotonic() + 10)   # she was busy a moment ago
    started = _time.monotonic()
    with pytest.raises(tts.CelineBusy):
        tts._synthesize('hi')
    assert _time.monotonic() - started < 1, 'one try, no busy wait, during the busy cool-down'


def test_missing_audio_skips_celine_until_restart(quiet_tts, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tts, '_synthesize', lambda text: calls.append(text) or b'wav')
    monkeypatch.setattr(tts, '_audio_missing', 'OSError: PortAudio library not found')
    tts.speak('One.')
    tts.speak('Two.')
    assert calls == [] and quiet_tts == [('system', 'One.'), ('system', 'Two.')]
    out = capsys.readouterr().out
    assert out.count("cannot play Celine's audio") == 1 and 'PortAudio' in out


def test_a_second_awareness_start_keeps_the_first_running(monkeypatch):
    from agent import awareness
    started = []
    monitor = object.__new__(awareness.AwarenessMonitor)
    stub = SimpleNamespace(start=lambda: started.append(1), stop=lambda: None)
    monitor.window = monitor.clipboard = monitor.files = stub
    monitor.iot = monitor.handtrack = None
    monitor.review_interval = 60
    monitor._stop = __import__('threading').Event()
    monkeypatch.setattr(awareness.AwarenessMonitor, '_review_loop', lambda self: None)
    monkeypatch.setattr(awareness, '_running', False)
    monitor.start()
    monitor.start()
    assert awareness.running() is True and len(started) == 3, 'the second start did nothing'
    monitor.stop()


@pytest.mark.parametrize('kind', ['ClipboardWatcher', 'ActiveWindowWatcher'])
def test_awareness_watchers_can_be_joined(kind):
    """They shadowed Thread._stop with an Event, so join() and is_alive() raised."""
    from agent import awareness
    watcher = getattr(awareness, kind)(awareness.AwarenessLog())
    watcher.interval = 0.05
    watcher.start()
    watcher.stop()
    watcher.join(timeout=5)
    assert watcher.is_alive() is False


def test_a_stopping_tracker_is_replaced_not_reused(monkeypatch):
    from agent import handtrack
    monkeypatch.setattr(config, 'HANDTRACK_ENABLED', True)
    import threading
    old = SimpleNamespace(is_alive=lambda: True, _stop_event=threading.Event(), join=lambda timeout=None: None)
    old._stop_event.set()
    monkeypatch.setattr(handtrack, '_active', old)
    created = []

    class Tracker:
        def __init__(self, log):
            created.append(self)
        def start(self):
            pass
        def latest_frame(self):
            return None
    monkeypatch.setattr(handtrack, 'HandTracker', Tracker)
    assert handtrack.start_standalone(start=False) is created[0]


def test_main_carries_on_when_awareness_fails_to_start():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8')
    i = src.index('            monitor.start()')
    block = src[i - 40:i + 900]
    assert 'except Exception' in block and 'monitor = None' in block and 'start_standalone(start=False)' in block


def test_a_companion_that_failed_to_start_says_why(monkeypatch):
    """A failed desktop window used to read as "enabled but not running" with no reason."""
    import importlib.util
    from agent import desktop_companion, live_status
    monkeypatch.setattr(desktop_companion, '_process', None)
    monkeypatch.setattr(desktop_companion, '_error', '')
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a: None if name == 'webview' else object())
    with pytest.raises(RuntimeError):
        desktop_companion.start('http://127.0.0.1:7860')
    st = desktop_companion.status()
    assert 'Setup-Apex-Screen-Companion.cmd' in st['error']
    monkeypatch.setattr(config, 'COMPANION_DESKTOP_ENABLED', True)
    block = live_status.status_block()
    assert 'Desktop Companion: failed to start (Run Setup-Apex-Screen-Companion.cmd' in block
    assert 'Companion failed' in live_status.status_block(compact=True)


def test_a_companion_window_that_crashed_says_so(monkeypatch):
    from agent import desktop_companion
    monkeypatch.setattr(desktop_companion, '_error', '')
    monkeypatch.setattr(desktop_companion, '_process', SimpleNamespace(poll=lambda: 3))
    assert 'exit code 3' in desktop_companion.status()['error']
    monkeypatch.setattr(desktop_companion, '_process', SimpleNamespace(poll=lambda: 0))
    assert desktop_companion.status()['error'] == ''
    monkeypatch.setattr(desktop_companion, '_process', None)
    assert desktop_companion.status()['error'] == ''
