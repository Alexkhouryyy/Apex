"""Launcher lifecycle tests: no GPU, microphone, accounts or real child processes."""
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py'
    spec = importlib.util.spec_from_file_location('apex_launcher_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module.Path, 'home', staticmethod(lambda: tmp_path))
    python = tmp_path / 'apex-qwen-fast-env/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(module, 'port_in_use', lambda *args: False)
    monkeypatch.setattr(module, 'launch_environment', lambda: {'APEX_SUPERVISED': '1'})
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
        if label == 'Apex dashboard':
            assert not accept({'service': 'apex', 'pid': 999, 'agent_ready': True})
            assert not accept({'service': 'apex', 'pid': 101, 'agent_ready': False})
            assert accept({'service': 'apex', 'pid': 101, 'agent_ready': True})
        else:
            assert not accept({'service': 'apex-qwen', 'model_loaded': False})
            assert accept({'service': 'apex-qwen', 'model_loaded': True})

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
    monkeypatch.setattr(launcher, 'wait_healthy', Mock(side_effect=[None, RuntimeError('dashboard failed')]))
    with pytest.raises(RuntimeError, match='dashboard failed'):
        launcher.main(['--fast', '--resident'])
    assert all(p.terminated for p in children)
    assert 'Dashboard: ready.' not in capsys.readouterr().out


def test_interrupt_stops_both_owned_processes(launcher, monkeypatch):
    children, _ = child_factory(launcher, monkeypatch, [None])
    monkeypatch.setattr(launcher, 'wait_healthy', Mock(side_effect=[None, KeyboardInterrupt()]))
    with pytest.raises(KeyboardInterrupt):
        launcher.main(['--fast', '--resident'])
    assert all(p.terminated for p in children)


def test_voice_crash_stops_agent(launcher, monkeypatch):
    children, _ = child_factory(launcher, monkeypatch, [None])
    children[0].returncode = 9
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    with pytest.raises(RuntimeError, match='Celine voice stopped'):
        launcher.main(['--fast', '--resident'])
    assert children[1].terminated


def test_dashboard_opt_out_skips_browser_and_health_check(launcher, monkeypatch):
    child_factory(launcher, monkeypatch, [0])
    monkeypatch.setattr(launcher, 'launch_environment', lambda: {'DASHBOARD_ENABLED': 'false'})
    health, browser = Mock(), Mock()
    monkeypatch.setattr(launcher, 'wait_healthy', health)
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    assert launcher.main(['--fast', '--resident', '--open']) == 0
    assert health.call_count == 1  # Only the voice service.
    browser.assert_not_called()


@pytest.mark.parametrize('exits, expected', [([42, 0], 0), ([3], 3)])
def test_only_explicit_restart_keeps_same_voice_process(launcher, monkeypatch, exits, expected):
    children, spawn = child_factory(launcher, monkeypatch, exits)
    monkeypatch.setattr(launcher, 'wait_healthy', Mock())
    assert launcher.main(['--fast', '--resident']) == expected
    assert spawn.call_count == len(exits) + 1
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
    ticks = iter([0, 0, 0, 1, 3])
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
    monkeypatch.setattr(server, '_agent_ref', None)
    assert asyncio.run(server.health()) == {'status': 'ok', 'service': 'apex',
                                           'pid': os.getpid(), 'agent_ready': False}
    monkeypatch.setattr(server, '_agent_ref', object())
    assert asyncio.run(server.health())['agent_ready'] is True
