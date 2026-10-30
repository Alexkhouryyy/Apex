"""Launcher lifecycle tests: no GPU, microphone, accounts or real child processes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from types import SimpleNamespace


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py'
    spec = importlib.util.spec_from_file_location('apex_launcher_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'WINDOWS', False)  # Fake processes in unit tests.
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module.Path, 'home', staticmethod(lambda: tmp_path))
    python = tmp_path / 'apex-qwen-fast-env/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(module, 'port_in_use', lambda *args: False)
    monkeypatch.setattr(module, 'launch_environment', lambda *a: {'APEX_SUPERVISED': '1'})
    monkeypatch.setattr(module, 'voice_health', lambda opener: {})
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    return module


class Process:
    def __init__(self, pid, exit_code=None):
        self.pid = pid
        self.returncode = None
        self.exit_code = exit_code
        self.polls = 0
        self.terminated = False

    def poll(self):
        self.polls += 1
        if self.exit_code is not None and self.polls >= 3:
            self.returncode = self.exit_code
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -1

    def wait(self, **kwargs):
        return self.returncode


def child_factory(launcher, monkeypatch, exits):
    voice = Process(100)
    children = [voice] + [Process(101 + i, code) for i, code in enumerate(exits)]
    spawn = Mock(side_effect=children)
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    return children, spawn


def test_full_start_checks_own_dashboard_and_opens_companion(launcher, monkeypatch, capsys):
    children, spawn = child_factory(launcher, monkeypatch, [0])

    def health(opener, url, accept, processes, label, timeout):
        assert label == 'Apex dashboard', 'Apex never waits for the voice'
        assert [name for name, _ in processes] == ['Apex'], 'a voice failure cannot abort the Apex wait'
        launch_id = spawn.call_args.kwargs['env']['APEX_LAUNCH_ID']
        assert not accept({'service': 'apex', 'pid': 101, 'agent_ready': True})
        assert not accept({'service': 'apex', 'launch_id': 'another-launch', 'agent_ready': True})
        assert not accept({'service': 'apex', 'launch_id': launch_id, 'agent_ready': False})
        assert accept({'service': 'apex', 'pid': 999, 'launch_id': launch_id, 'agent_ready': True})

    monkeypatch.setattr(launcher, 'wait_healthy', health)
    browser = Mock()
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    assert launcher.main(['--fast', '--resident', '--open']) == 0
    assert spawn.call_args_list[1].args[0][-1] == '--resident'
    assert spawn.call_args_list[1].kwargs['env']['APEX_SUPERVISED'] == '1'
    browser.assert_called_once_with('http://127.0.0.1:7860/companion')
    assert children[0].terminated
    assert 'Dashboard: ready.' in capsys.readouterr().out


def test_dashboard_failure_stops_both_children_without_ready_message(launcher, monkeypatch, capsys):
    children, _ = child_factory(launcher, monkeypatch, [None])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock(side_effect=[RuntimeError('dashboard failed')]))
    with pytest.raises(RuntimeError, match='dashboard failed'):
        launcher.main(['--fast', '--resident'])
    assert all(p.terminated for p in children)
    assert 'Dashboard: ready.' not in capsys.readouterr().out


def test_interrupt_stops_both_owned_processes(launcher, monkeypatch):
    children, _ = child_factory(launcher, monkeypatch, [None])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock(side_effect=[KeyboardInterrupt()]))
    with pytest.raises(KeyboardInterrupt):
        launcher.main(['--fast', '--resident'])
    assert all(p.terminated for p in children)


def test_a_voice_crash_keeps_apex_running(launcher, monkeypatch, capsys):
    children, _ = child_factory(launcher, monkeypatch, [0])
    children[0].returncode = 9                       # Celine's server died (e.g. out of GPU memory)
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0, 'Apex ran until it exited by itself'
    assert not children[1].terminated
    out = capsys.readouterr().out
    assert 'Celine voice stopped (exit 9)' in out and "this PC's own voice" in out
    assert out.count('Celine voice stopped') == 1


def test_voice_ready_is_announced_without_ever_blocking_apex(launcher, monkeypatch, capsys):
    child_factory(launcher, monkeypatch, [0])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: {'service': 'apex-qwen', 'model_loaded': True})
    assert launcher.main(['--fast', '--resident']) == 0
    assert 'Celine voice: ready.' in capsys.readouterr().out


def test_no_voice_environment_still_starts_apex_with_your_own_voice_settings(launcher, monkeypatch, capsys, tmp_path):
    (tmp_path / 'apex-qwen-fast-env/Scripts/python.exe').unlink()
    agent = Process(101, 0)
    spawn = Mock(side_effect=[agent])
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    calls = []
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: calls.append(a) or {'APEX_SUPERVISED': '1'})
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    assert spawn.call_count == 1 and spawn.call_args.args[0][-1] == '--resident', 'only Apex was started'
    assert calls[-1] == (False,), "TTS settings from .env are kept when there is no local voice"
    assert 'Celine voice: not installed' in capsys.readouterr().out


@pytest.mark.parametrize('health, local, says', [
    ({'service': 'apex-qwen', 'model_loaded': True}, True, 'already running'),
    ({}, False, 'used by another program'),
])
def test_an_occupied_voice_port_is_reused_or_skipped(launcher, monkeypatch, capsys, health, local, says):
    agent = Process(101, 0)
    spawn = Mock(side_effect=[agent])
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    monkeypatch.setattr(launcher, 'port_in_use', lambda port, host='127.0.0.1': port == 17494)
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: health)
    calls = []
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: calls.append(a) or {'APEX_SUPERVISED': '1'})
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    assert spawn.call_count == 1 and calls[-1] == (local,)
    assert says in capsys.readouterr().out


def test_dashboard_opt_out_skips_browser_and_health_check(launcher, monkeypatch):
    child_factory(launcher, monkeypatch, [0])
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: {'DASHBOARD_ENABLED': 'false'})
    health, browser = Mock(), Mock()
    monkeypatch.setattr(launcher, 'wait_healthy', health)
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    assert launcher.main(['--fast', '--resident', '--open']) == 0
    assert health.call_count == 0  # Nothing waits: no dashboard, and never the voice.
    browser.assert_not_called()


@pytest.mark.parametrize('exits, expected', [([42, 0], 0), ([3], 3)])
def test_only_explicit_restart_keeps_same_voice_process(launcher, monkeypatch, exits, expected):
    children, spawn = child_factory(launcher, monkeypatch, exits)
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == expected
    assert spawn.call_count == len(exits) + 1
    launch_ids = [c.kwargs['env']['APEX_LAUNCH_ID'] for c in spawn.call_args_list[1:]]
    assert len(set(launch_ids)) == len(exits)
    assert children[0].terminated


def test_restart_burst_is_bounded(launcher, monkeypatch):
    children, spawn = child_factory(launcher, monkeypatch, [42] * 5)
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    with pytest.raises(RuntimeError, match='Too many Apex restarts'):
        launcher.main(['--fast'])
    assert spawn.call_count == 6
    assert children[0].terminated


def test_busy_port_does_not_spawn_or_kill_anything(launcher, monkeypatch):
    monkeypatch.setattr(launcher, 'port_in_use', lambda *args: True)
    spawn = Mock()
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    with pytest.raises(RuntimeError, match='occupied'):
        launcher.main(['--fast'])
    spawn.assert_not_called()


def test_wait_requires_health_predicate_and_rejects_failed_process(launcher, monkeypatch):
    opener = Mock()
    opener.open.side_effect = [io.StringIO(json.dumps(h)) for h in
                              ({'ready': False}, {'ready': True})]
    process = Process(100)
    launcher.wait_healthy(opener, 'http://localhost/health', lambda h: h['ready'],
                          [('test', process)], 'test', 3)
    assert opener.open.call_count == 2
    process.returncode = 9
    with pytest.raises(RuntimeError, match='exit 9'):
        launcher.wait_healthy(opener, '', lambda h: True, [('test', process)], 'test', 3)


def test_wait_times_out_when_endpoint_never_becomes_ready(launcher, monkeypatch):
    import itertools
    ticks = itertools.chain([0, 0, 0, 1], itertools.repeat(3))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: next(ticks))
    opener = Mock()
    opener.open.side_effect = OSError('unavailable')
    with pytest.raises(RuntimeError, match='did not become ready'):
        launcher.wait_healthy(opener, '', lambda h: True, [], 'dashboard', 2)


def test_custom_dashboard_port_and_tokenless_fallback(launcher):
    assert launcher.dashboard_address({'DASHBOARD_HOST': '0.0.0.0', 'DASHBOARD_PORT': '8001'}) == (
        '127.0.0.1', 8001, 'http://127.0.0.1:8001')
    assert launcher.dashboard_address({'DASHBOARD_HOST': '192.168.1.5', 'DASHBOARD_TOKEN': 'test'})[0] == '192.168.1.5'


def test_environment_preserves_explicit_wake_opt_out(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('apex_launcher_env',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.delenv('CELINE_WAKE_ENABLED', raising=False)
    (tmp_path / '.env').write_text('CELINE_WAKE_ENABLED=false\nDASHBOARD_PORT=8123\n')
    env = module.launch_environment()
    assert env['CELINE_WAKE_ENABLED'] == 'false'
    monkeypatch.setenv('CELINE_WAKE_ENABLED', 'true')
    assert module.launch_environment()['CELINE_WAKE_ENABLED'] == 'true'


def test_resident_shares_listener_routes_and_honors_mute(monkeypatch):
    from app import resident
    from app.hotkey import GlobalHotkeys
    from agent import look_now
    from voice import wake
    for key, value in {'DASHBOARD_ENABLED': True, 'WAKE_WORD_ENABLED': True,
                       'CELINE_WAKE_ENABLED': True, 'WAKE_PHRASES': ['apex'],
                       'CELINE_WAKE_PHRASES': ['hey celly']}.items():
        monkeypatch.setattr(resident.config, key, value)
    listener = Mock()
    factory = Mock(return_value=listener)
    monkeypatch.setattr(wake, 'WakeWordListener', factory)
    celine, apex, look = Mock(), Mock(), Mock()
    monkeypatch.setattr(look_now, 'make_wake_handler', Mock(return_value=celine))
    monkeypatch.setattr(look_now, 'on_hotkey', look)
    state = resident.ResidentState()
    keys = GlobalHotkeys()
    assert resident._configure_attention(keys, apex, state.is_muted) is listener
    factory.assert_called_once_with(wake_phrases=['hey celly', 'apex'])
    route = listener.start.call_args.kwargs['on_wake']
    route('hey celly help')
    route('apex hello')
    route('apexes')
    celine.assert_called_once_with('hey celly help')
    apex.assert_called_once_with('apex hello')
    state.mute(-1)
    route('hey celly muted')
    route('apex muted')
    keys._bindings[resident.config.CELINE_HOTKEY]()
    assert celine.call_count == apex.call_count == 1
    look.assert_not_called()
    state.mute(0)
    keys._bindings[resident.config.CELINE_HOTKEY]()
    look.assert_called_once()


def test_disabled_wake_features_never_start_listener(monkeypatch):
    from app import resident
    from voice import wake
    monkeypatch.setattr(resident.config, 'WAKE_WORD_ENABLED', False)
    monkeypatch.setattr(resident.config, 'CELINE_WAKE_ENABLED', False)
    factory = Mock()
    monkeypatch.setattr(wake, 'WakeWordListener', factory)
    assert resident._configure_attention(Mock(), Mock(), lambda: False) is None
    factory.assert_not_called()


def test_dashboard_health_identifies_process_and_agent(monkeypatch):
    import asyncio
    import os
    from dashboard import server
    monkeypatch.setenv('APEX_LAUNCH_ID', 'test-launch-id')
    monkeypatch.setattr(server, '_agent_ref', None)
    assert asyncio.run(server.health()) == {'status': 'ok', 'service': 'apex',
                                           'pid': os.getpid(), 'launch_id': 'test-launch-id',
                                           'agent_ready': False}
    monkeypatch.setattr(server, '_agent_ref', object())
    assert asyncio.run(server.health())['agent_ready'] is True


def test_windows_shutdown_targets_only_owned_tree(launcher, monkeypatch):
    monkeypatch.setattr(launcher, 'WINDOWS', True)
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    process = Process(123)
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(subprocess, 'run', run)
    launcher.stop(process)
    assert run.call_args.args[0] == ['taskkill', '/PID', '123', '/T', '/F']
    assert run.call_args.kwargs['creationflags'] == subprocess.CREATE_NO_WINDOW
    assert not process.terminated  # Never kill just the venv redirector.


def test_windows_shutdown_failure_is_reported(launcher, monkeypatch):
    monkeypatch.setattr(launcher, 'WINDOWS', True)
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'run', Mock(return_value=Mock(returncode=1, stderr='Access denied')))
    with pytest.raises(RuntimeError, match='Could not stop owned process tree 123'):
        launcher.stop(Process(123))


def test_voice_cleanup_still_runs_if_agent_cleanup_fails(launcher, monkeypatch):
    children, _ = child_factory(launcher, monkeypatch, [None])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock(side_effect=[RuntimeError('startup failed')]))
    stop = Mock(side_effect=[RuntimeError('cannot stop agent'), None])
    monkeypatch.setattr(launcher, 'stop', stop)
    with pytest.raises(RuntimeError, match='cannot stop agent'):
        launcher.main(['--fast', '--resident'])
    assert [c.args[0] for c in stop.call_args_list] == [children[1], children[0]]


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows venv redirector regression')
def test_real_windows_venv_health_and_tree_shutdown(tmp_path):
    """Real child + loopback HTTP, without booting Apex, models or devices."""
    spec = importlib.util.spec_from_file_location('apex_launcher_windows',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    script = tmp_path / 'health_child.py'
    metadata = tmp_path / 'child.json'
    script.write_text('''import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'service': 'apex', 'pid': os.getpid(),
            'launch_id': os.environ['APEX_LAUNCH_ID'], 'agent_ready': True}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
server = HTTPServer(('127.0.0.1', 0), Handler)
# A bounded lifetime guarantees cleanup even in sandboxes that deny taskkill.
timer = threading.Timer(15, server.shutdown)
timer.daemon = True
timer.start()
Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'port': server.server_port}))
server.serve_forever()
server.server_close()
''', encoding='utf-8')
    env = dict(os.environ, APEX_LAUNCH_ID='windows-test-launch')
    process = subprocess.Popen([sys.executable, str(script), str(metadata)], env=env)
    child = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if metadata.exists():
                try:
                    child = json.loads(metadata.read_text())
                    break
                except ValueError:
                    pass
            if process.poll() is not None:
                pytest.fail('Test child exited before serving health')
            time.sleep(.05)
        assert child is not None, 'Test child failed to start'
        opener = module.urllib.request.build_opener(module.urllib.request.ProxyHandler({}))
        module.wait_healthy(opener, f"http://127.0.0.1:{child['port']}/health",
            lambda h: h.get('launch_id') == env['APEX_LAUNCH_ID'] and h.get('agent_ready'),
            [('test child', process)], 'test child', 5)
        try:
            module.stop(process)
        except RuntimeError as exc:
            if 'access denied' in str(exc).lower() or 'access is denied' in str(exc).lower():
                pytest.skip('Windows sandbox denied taskkill; readiness passed, tree shutdown requires a normal terminal')
            raise
        assert process.poll() is not None
        assert not module.port_in_use(child['port']), 'Real interpreter was orphaned'
    finally:
        # Do not leave a child behind when taskkill is unavailable. The test
        # server's own bounded lifetime also handles a dead redirector.
        process.wait(timeout=20)
        if child:
            deadline = time.monotonic() + 20
            while module.port_in_use(child['port']) and time.monotonic() < deadline:
                time.sleep(.05)
            assert not module.port_in_use(child['port'])


def test_no_launcher_injects_feature_defaults(tmp_path, monkeypatch):
    """Defaults live in config.py. A launcher that injected them into the
    environment made them beat a setting saved in .env before a dashboard
    Restart (load_dotenv never overrides the environment)."""
    from scripts import start_screen_companion as startup
    spec = importlib.util.spec_from_file_location('apex_launcher_defaults',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    names = ('COMPANION_DESKTOP_ENABLED', 'CELINE_WAKE_ENABLED', 'WAKE_WORD_ENABLED')
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(startup, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('DASHBOARD_PORT=8123\n')
    for env in (module.launch_environment(), startup.environment()):
        assert not any(name in env for name in names)
    from scripts import apex_supervisor
    seen = {}
    monkeypatch.setattr(apex_supervisor.subprocess, 'run',
                        lambda argv, cwd, env: seen.update(env) or SimpleNamespace(returncode=0))
    assert apex_supervisor.run(['--resident']) == 0
    assert not any(name in seen for name in names)


def test_config_holds_one_set_of_defaults(monkeypatch):
    import importlib
    import config as cfg
    for name in ('COMPANION_DESKTOP_ENABLED', 'CELINE_WAKE_ENABLED', 'WAKE_WORD_ENABLED'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('dotenv.load_dotenv', lambda *a, **k: False)
    try:
        fresh = importlib.reload(cfg)
        assert fresh.COMPANION_DESKTOP_ENABLED is True
        assert fresh.CELINE_WAKE_ENABLED is False and fresh.WAKE_WORD_ENABLED is False
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)


# ---------------------------------------------------------------- what the stress test found

class _Opener:
    def __init__(self, behave):
        self.behave = behave
    def open(self, url, timeout=None):
        return self.behave()


@pytest.mark.filterwarnings('error::pytest.PytestUnhandledThreadExceptionWarning')
@pytest.mark.parametrize('behave', [
    lambda: (_ for _ in ()).throw(__import__('http.client').client.BadStatusLine('SSH-2.0-not-http')),
    lambda: (_ for _ in ()).throw(__import__('http.client').client.IncompleteRead(b'x' * 23, 177)),
    lambda: (_ for _ in ()).throw(__import__('http.client').client.LineTooLong('header line')),
    lambda: (_ for _ in ()).throw(RuntimeError('anything at all')),
])
def test_no_reply_on_the_voice_port_can_crash_the_launcher(launcher, behave):
    """A malformed reply used to escape voice_health and stop a healthy Apex."""
    import importlib
    spec = importlib.util.spec_from_file_location('apex_launcher_real_vh',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    assert real.voice_health(_Opener(behave)) == {}


def test_a_trickling_voice_port_cannot_hang_the_launcher():
    import time as _time
    spec = importlib.util.spec_from_file_location('apex_launcher_real_trickle',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    def trickle():
        _time.sleep(10)              # one byte at a time: never finishes in time
    started = _time.monotonic()
    assert real.voice_health(_Opener(trickle), deadline=0.5) == {}
    assert _time.monotonic() - started < 2


def test_a_damaged_voice_python_still_starts_apex(launcher, monkeypatch, capsys):
    agent = Process(101, 0)
    def spawn(argv, **kwargs):
        if 'qwen' in str(argv[0]):
            raise OSError(8, 'Exec format error')
        return agent
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    calls = []
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: calls.append(a) or {'APEX_SUPERVISED': '1'})
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    assert calls[-1] == (False,) and 'Celine voice: could not start' in capsys.readouterr().out


def test_after_celine_dies_a_restarted_apex_no_longer_points_at_her(launcher, monkeypatch, capsys):
    children, spawn = child_factory(launcher, monkeypatch, [42, 0])
    children[0].returncode = 9
    calls = []
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: calls.append(a) or {'APEX_SUPERVISED': '1'})
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    assert calls[-1] == (False,), 'the second Apex keeps its own voice settings'
    out = capsys.readouterr().out
    assert 'Restart requested.' in out and 'Keeping Celine voice warm' not in out


def test_an_external_voice_that_stops_answering_is_reported(launcher, monkeypatch, capsys):
    agent = Process(101)
    polls = {'n': 0}
    def agent_poll():
        polls['n'] += 1
        return 0 if polls['n'] > 40 else None
    agent.poll = agent_poll
    agent.returncode = 0
    monkeypatch.setattr(launcher.subprocess, 'Popen', Mock(side_effect=[agent]))
    monkeypatch.setattr(launcher, 'port_in_use', lambda port, host='127.0.0.1': port == 17494)
    answers = iter([{'service': 'apex-qwen', 'model_loaded': True}] * 2 + [{}] * 50)
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: next(answers))
    clock = {'t': 0.0}
    def tick():
        clock['t'] += 10
        return clock['t']
    monkeypatch.setattr(launcher.time, 'monotonic', tick)
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    out = capsys.readouterr().out
    assert 'already running; using it' in out and 'Celine voice: ready.' in out
    assert out.count('Celine voice stopped answering') == 1


# ---------------------------------------------------------------- round 2 of the stress test

def _real_launcher(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probes_never_pile_up_against_a_trickling_peer():
    """720 stuck threads an hour, each holding a socket, until the process ran out of files."""
    import threading
    import time as _time
    real = _real_launcher('apex_launcher_pileup')
    gate = threading.Event()
    opened = []

    def trickle():
        opened.append(1)
        gate.wait(10)
    for _ in range(20):
        assert real.probe_json(_Opener(trickle), 'http://127.0.0.1:17494/health', deadline=0.05) == {}
    assert len(opened) == 1, 'one stuck probe per address, not one per check'
    gate.set()
    _time.sleep(0.2)
    real.probe_json(_Opener(lambda: (_ for _ in ()).throw(OSError('refused'))), 'http://127.0.0.1:17494/health', 0.5)


def test_waiting_for_the_dashboard_respects_its_overall_timeout_against_a_trickle():
    import threading
    import time as _time
    real = _real_launcher('apex_launcher_wait_trickle')
    gate = threading.Event()
    started = _time.monotonic()
    with pytest.raises(RuntimeError, match='did not become ready'):
        real.wait_healthy(_Opener(lambda: gate.wait(30)), 'http://127.0.0.1:7860/health', lambda h: True, [], 'Apex', 2)
    gate.set()
    assert _time.monotonic() - started < 6


def test_starting_hint_only_while_the_voice_is_not_ready(launcher, monkeypatch):
    children, spawn = child_factory(launcher, monkeypatch, [42, 0])
    monkeypatch.setattr(launcher, 'launch_environment', lambda *a: {'APEX_SUPERVISED': '1', 'APEX_LOCAL_VOICE': 'inherited'})
    import itertools
    states = itertools.chain([{}], itertools.repeat({'service': 'apex-qwen', 'model_loaded': True}))
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: next(states))
    clock = {'t': 0.0}
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: clock.__setitem__('t', clock['t'] + 10) or clock['t'])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    first, second = (c.kwargs['env'] for c in spawn.call_args_list[1:3])
    assert first['APEX_LOCAL_VOICE'] == 'starting', 'the first Apex starts while she is warming'
    assert 'APEX_LOCAL_VOICE' not in second, 'after "ready", a restarted Apex is not told she is starting'


def test_one_slow_answer_from_a_ready_voice_is_not_a_death(launcher, monkeypatch, capsys):
    agent = Process(101)
    polls = {'n': 0}
    agent.poll = lambda: (polls.__setitem__('n', polls['n'] + 1) or (0 if polls['n'] > 60 else None))
    agent.returncode = 0
    monkeypatch.setattr(launcher.subprocess, 'Popen', Mock(side_effect=[Process(100), agent]))
    ok = {'service': 'apex-qwen', 'model_loaded': True}
    import itertools
    answers = itertools.chain([ok, {}, ok, {}, ok], itertools.repeat(ok))   # busy blips between good answers
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: next(answers))
    clock = {'t': 0.0}
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: clock.__setitem__('t', clock['t'] + 40) or clock['t'])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    assert 'stopped answering' not in capsys.readouterr().out


def test_a_reused_ready_voice_is_ready_from_the_start(launcher, monkeypatch, capsys):
    agent = Process(101, 0)
    monkeypatch.setattr(launcher.subprocess, 'Popen', Mock(side_effect=[agent]))
    monkeypatch.setattr(launcher, 'port_in_use', lambda port, host='127.0.0.1': port == 17494)
    monkeypatch.setattr(launcher, 'voice_health', lambda opener: {'service': 'apex-qwen', 'model_loaded': True})
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == 0
    out = capsys.readouterr().out
    assert out.index('already running; using it') < out.index('Celine voice: ready.') < out.index('Starting Apex')


def test_starting_is_announced_only_once_the_voice_process_exists(tmp_path, monkeypatch, capsys):
    real = _real_launcher('apex_launcher_order')
    python = tmp_path / 'python.exe'
    python.touch()
    monkeypatch.setattr(real, 'port_in_use', lambda *a: False)
    monkeypatch.setattr(real.subprocess, 'Popen', Mock(side_effect=OSError(8, 'Exec format error')))
    assert real.start_voice(python, 'scripts/qwen_server.py', None) == (None, False)
    out = capsys.readouterr().out
    assert 'could not start' in out and 'Starting Celine voice' not in out
