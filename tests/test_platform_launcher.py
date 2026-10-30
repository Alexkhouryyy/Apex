"""Platform startup must identify its services and never trigger preparation."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from scripts import run_apex_platform as platform


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(platform, 'ROOT', tmp_path)
    monkeypatch.setattr(platform.launcher, 'WINDOWS', False)
    monkeypatch.setattr(platform.launcher, 'launch_environment', lambda: {'APEX_APOCALYPSE':'1','APEX_APOCALYPSE_HOME':str(tmp_path)})
    monkeypatch.setattr(platform.launcher, 'port_in_use', lambda *a: False)
    monkeypatch.setattr(platform, 'start_cached_nomad', Mock(return_value=None))
    children=[]
    def spawn(args, env):
        p=NS(args=args,env=env,poll=Mock(return_value=None),returncode=7,pid=100+len(children))
        children.append(p)
        return p
    monkeypatch.setattr(platform,'spawn',spawn)
    stopped=[]
    monkeypatch.setattr(platform,'stop_owned',lambda p:stopped.append(p))
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock())
    monkeypatch.setattr(platform.webbrowser,'open',Mock())
    def interrupted(_): raise KeyboardInterrupt()
    monkeypatch.setattr(platform.time,'sleep',interrupted)
    return children,stopped


def test_full_start_checks_identity_separates_modes_and_stops_only_owned_children(rig,monkeypatch,capsys):
    children,stopped=rig
    def wait(opener,url,accept,processes,label,timeout):
        env=processes[0][1].env
        base=dict(service='apex',agent_ready=True,platform_id=env['APEX_PLATFORM_ID'],launch_id=env.get('APEX_LAUNCH_ID'))
        assert accept(base)
        assert not accept({**base,'platform_id':'unrelated'})
        assert not accept({**base,'agent_ready':False})
        if 'Apocalypse' in label: assert not accept({**base,'launch_id':'old'})
    monkeypatch.setattr(platform.launcher,'wait_healthy',wait)
    with pytest.raises(KeyboardInterrupt):platform.main(['--open'])
    assert len(children)==2 and stopped==children[::-1]
    assert children[0].env['APEX_APOCALYPSE']=='1'
    assert children[1].env['APEX_APOCALYPSE']=='0'
    assert children[1].env['HF_HUB_OFFLINE']==children[1].env['TRANSFORMERS_OFFLINE']=='1'
    assert '--resident' in children[1].args and '--fast' in children[1].args
    assert platform.webbrowser.open.call_args_list[0].args==('http://127.0.0.1:7862/apocalypse',)
    assert 'No model, book, course or map downloads' in capsys.readouterr().out


def test_offline_only_does_not_start_cloud_session_or_celine(rig):
    children,stopped=rig
    with pytest.raises(KeyboardInterrupt):platform.main(['--offline-only','--no-nomad'])
    assert len(children)==1 and stopped==children
    platform.start_cached_nomad.assert_not_called()


def test_busy_port_changes_nothing(rig,monkeypatch):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'port_in_use',lambda *a:True)
    with pytest.raises(RuntimeError,match='already running'):platform.main([])
    assert not children and not stopped
    platform.start_cached_nomad.assert_not_called()


def test_apex_failing_to_start_keeps_the_apocalypse_library_running(rig,monkeypatch,capsys):
    """Celine's voice model failing on the GPU once shut the working library down too."""
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=[None,RuntimeError('not ready')]))
    with pytest.raises(KeyboardInterrupt):platform.main([])         # still running until Ctrl+C
    out=capsys.readouterr().out
    assert 'Apex + Celine: did not start (not ready). Apocalypse remains available.' in out
    assert 'Ready: Apocalypse.' in out
    assert stopped==[children[1],children[0]],'the failed one at once, the library only at Ctrl+C'


def test_apocalypse_failing_to_start_still_starts_apex(rig,monkeypatch,capsys):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=[RuntimeError('no library'),None]))
    with pytest.raises(KeyboardInterrupt):platform.main([])
    out=capsys.readouterr().out
    assert 'Apocalypse: did not start (no library). Apex continues without it.' in out and 'Ready: Apex + Celine.' in out
    assert len(children)==2


def test_nothing_starting_is_an_error(rig,monkeypatch):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=RuntimeError('down')))
    with pytest.raises(RuntimeError,match='No Apex service is running'):platform.main([])
    assert stopped==children


def test_a_service_crash_leaves_the_other_running(rig,monkeypatch,capsys):
    children,stopped=rig
    def health(*a):
        if len(children)==2:children[0].poll.return_value=7
    monkeypatch.setattr(platform.launcher,'wait_healthy',health)
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert 'Apocalypse stopped (exit 7). Apex + Celine keeps running.' in capsys.readouterr().out
    assert stopped==[children[1]],'only the one still running is stopped at Ctrl+C'


def test_when_every_service_has_stopped_the_launcher_exits(rig,monkeypatch):
    children,stopped=rig
    def die(_):
        for c in children: c.poll.return_value=3
    monkeypatch.setattr(platform.time,'sleep',die)
    with pytest.raises(RuntimeError,match='Every Apex service has stopped'):platform.main([])


# ---------------------------------------------------------------- what the stress test found

def test_both_services_start_at_once(rig,monkeypatch):
    """A hung Apocalypse used to delay Apex + Celine by its full 240 s wait."""
    children,stopped=rig
    seen=[]
    def wait(opener,url,accept,processes,label,timeout):
        seen.append((label,len(children)))
    monkeypatch.setattr(platform.launcher,'wait_healthy',wait)
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert seen[0]==('Apocalypse dashboard',2),'Apex + Celine was already started when the first wait began'


def test_two_services_crashing_at_once_are_both_reported_as_stopped(rig,monkeypatch,capsys):
    children,stopped=rig
    def sleep(_):
        for c in children: c.poll.return_value=7   # both die in the same second
    monkeypatch.setattr(platform.time,'sleep',sleep)
    with pytest.raises(RuntimeError,match='Every Apex service has stopped'):platform.main([])
    out=capsys.readouterr().out
    assert 'Apocalypse stopped (exit 7).' in out and 'Apex + Celine stopped (exit 7).' in out
    assert 'keeps running' not in out


def test_a_service_that_died_while_the_other_was_starting_is_not_called_ready(rig,monkeypatch,capsys):
    children,stopped=rig
    def wait(opener,url,accept,processes,label,timeout):
        if label=='Apex + Celine':
            children[0].poll.return_value=1          # Apocalypse dies during the Apex wait
            raise RuntimeError('not ready')
    monkeypatch.setattr(platform.launcher,'wait_healthy',wait)
    with pytest.raises(RuntimeError,match='No Apex service is running'):platform.main([])
    out=capsys.readouterr().out
    assert 'Apocalypse remains available' not in out and 'Ready: Apocalypse' not in out


def test_offline_only_failure_does_not_promise_apex(rig,monkeypatch,capsys):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=RuntimeError('no library')))
    with pytest.raises(RuntimeError,match='No Apex service is running'):platform.main(['--offline-only','--no-nomad'])
    assert 'Apex continues without it' not in capsys.readouterr().out


def test_celine_s_voice_port_does_not_block_the_platform(rig,monkeypatch):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'port_in_use',lambda port,host='127.0.0.1':port==17494)
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert len(children)==2


@pytest.mark.parametrize('error',[__import__('http.client').client.BadStatusLine('x'),ValueError('not json'),TypeError('odd')])
def test_odd_health_answers_mean_not_ready_never_a_crash(monkeypatch,error):
    class Opener:
        def open(self,url,timeout=None): raise error
    assert platform.health(Opener(),'http://127.0.0.1:1/health')=={}


def test_a_second_ctrl_c_during_shutdown_still_stops_every_service(rig,monkeypatch):
    children,stopped=rig
    attempts=[]
    def stop(p):
        attempts.append(p)
        if len(attempts)==1:
            raise KeyboardInterrupt()             # the owner presses Ctrl+C again
    monkeypatch.setattr(platform,'stop_owned',stop)
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert set(map(id,attempts))=={id(c) for c in children},'both services got a stop, the first one twice'


def test_an_unexpected_error_while_apocalypse_starts_still_starts_apex(rig,monkeypatch,capsys):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=[ValueError('odd reply'),None]))
    with pytest.raises(KeyboardInterrupt):platform.main([])
    out=capsys.readouterr().out
    assert 'Apocalypse: did not start (odd reply)' in out and 'Ready: Apex + Celine.' in out


# ---------------------------------------------------------------- round 2 of the stress test

def test_a_ctrl_c_while_a_failed_service_is_being_stopped_still_stops_it_later(rig,monkeypatch):
    children,stopped=rig
    calls=[]
    def stop(p):
        calls.append(p)
        if len(calls)==1:
            raise KeyboardInterrupt()          # pressed while drop() was stopping Apex
    monkeypatch.setattr(platform,'stop_owned',stop)
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=[None,RuntimeError('not ready')]))
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert calls.count(children[1])>=2,'the half-stopped Apex is still owned, so the final cleanup stops it again'


def test_ctrl_c_is_ignored_during_cleanup_and_restored_after(rig,monkeypatch):
    import signal
    children,stopped=rig
    seen=[]
    def stop(p):
        seen.append(signal.getsignal(signal.SIGINT))
    monkeypatch.setattr(platform,'stop_owned',stop)
    before=signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):platform.main([])
    assert seen and all(s==signal.SIG_IGN for s in seen),'a second Ctrl+C cannot interrupt the stops'
    assert signal.getsignal(signal.SIGINT)==before,'and Ctrl+C works again afterwards'


def test_a_service_that_was_ready_then_died_is_reported(rig,monkeypatch,capsys):
    children,stopped=rig
    def wait(opener,url,accept,processes,label,timeout):
        if label=='Apex + Celine':
            children[0].poll.return_value=4      # the ready Apocalypse dies meanwhile
            raise RuntimeError('not ready')
    monkeypatch.setattr(platform.launcher,'wait_healthy',wait)
    with pytest.raises(RuntimeError,match='No Apex service is running'):platform.main([])
    out=capsys.readouterr().out
    assert 'Apocalypse: ready.' in out and 'Apocalypse stopped (exit 7).' in out
