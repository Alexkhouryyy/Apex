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
        env=children[-1].env
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


def test_second_service_startup_failure_cleans_up_both(rig,monkeypatch):
    children,stopped=rig
    monkeypatch.setattr(platform.launcher,'wait_healthy',Mock(side_effect=[None,RuntimeError('not ready')]))
    with pytest.raises(RuntimeError,match='not ready'):platform.main([])
    assert len(children)==2 and stopped==children[::-1]


def test_service_crash_stops_remaining_owned_service(rig,monkeypatch):
    children,stopped=rig
    def health(*a):
        if len(children)==2:children[0].poll.return_value=7
    monkeypatch.setattr(platform.launcher,'wait_healthy',health)
    with pytest.raises(RuntimeError,match='Apocalypse stopped'):platform.main([])
    assert stopped==children[::-1]


@pytest.mark.parametrize('missing', ['engine','images',None])
def test_cached_nomad_never_pulls_builds_or_regenerates_compose(tmp_path,monkeypatch,missing):
    monkeypatch.setattr(platform.launcher,'WINDOWS',False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'local'))
    docker=tmp_path/'local/Programs/DockerDesktop/resources/bin/docker.exe'
    docker.parent.mkdir(parents=True);docker.touch()
    compose=tmp_path/'nomad/compose.json';compose.parent.mkdir();compose.write_text('{"fixture":true}')
    original=compose.read_bytes()
    commands=[]
    def run(args,**kwargs):
        commands.append(args)
        if args[1]=='info':return NS(returncode=1 if missing=='engine' else 0,stdout='linux\n')
        if '--images' in args:return NS(returncode=0,stdout='mysql:8.0\nredis:7-alpine\napex-nomad:pinned\n')
        assert args[1:3]==['image','inspect']
        return NS(returncode=1 if missing=='images' else 0,stdout='')
    monkeypatch.setattr(platform.subprocess,'run',run)
    popen=Mock(return_value='cached-process');monkeypatch.setattr(platform.subprocess,'Popen',popen)
    result=platform.start_cached_nomad(tmp_path)
    if missing:
        assert result is None;popen.assert_not_called()
    else:
        assert result=='cached-process'
        assert popen.call_args.args[0][-5:]==['up','-d','--pull','never','--no-build']
    assert compose.read_bytes()==original
    assert not any('build' in c or 'pull' in c for c in commands)


def test_health_platform_identity_is_optional(monkeypatch):
    import asyncio
    from dashboard import server
    monkeypatch.setenv('APEX_PLATFORM_ID','fixture-platform')
    assert asyncio.run(server.health())['platform_id']=='fixture-platform'
