"""Desktop ownership, frame provenance, hotkey responsiveness and real Windows painting."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from agent import desktop_companion as desktop, look_now, screen_guidance as guidance
from app.companion_desktop import Bridge, MicrophonePermission
from app.screen_overlay import CLICK_THROUGH, paint
from tests.test_companion import agent as core_agent


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(desktop, '_process', SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(desktop, '_session', 'private-desktop-session')
    monkeypatch.setattr(desktop, '_events', [])
    monkeypatch.setattr(desktop, '_results', {})
    monkeypatch.setattr(desktop, '_seq', 0)
    monkeypatch.setattr(desktop, '_shown', True)
    monkeypatch.setattr(desktop, '_mode', 'screen')
    monkeypatch.setattr(desktop, '_url', '')
    desktop.connected()
    yield
    look_now.reset()


def frame():
    return guidance.frame_info({'left': -1920, 'top': -200, 'width': 1920, 'height': 1080})


SHAPES = [{'type': 'arrow', 'points': [[.1, .2], [.4, .6]], 'label': 'Click here'}]


@pytest.mark.parametrize('url', ['https://127.0.0.1:7860', 'http://example.com', 'http://user:secret@localhost',
                                'http://localhost:7860?token=secret', 'file:///tmp/app'])
def test_native_transport_rejects_nonlocal_or_credential_urls(url):
    with pytest.raises(ValueError):
        desktop.local_url(url)


@pytest.mark.parametrize('points', [[[float('nan'), .2]], [[float('inf'), 0]], [[-1, .2]], [[True, .2]], [[1.1, .2]]])
def test_screen_coordinates_are_finite_normalized_numbers(points):
    with pytest.raises(ValueError):
        guidance.validate_shapes([{'type': 'label', 'points': points, 'label': 'target'}])


@pytest.mark.parametrize('shape', [
    {'type': 'rectangle', 'points': [[.1, .2]]},
    {'type': 'polygon', 'points': [[.1, .2], [.3, .4]]},
    {'type': 'label', 'points': [[.1, .2]], 'label': ''},
    {'type': 'line', 'points': [[.1, .2], [.3, .4]], 'color': '<script>'},
])
def test_malformed_shapes_are_refused(shape):
    with pytest.raises(ValueError):
        guidance.validate_shapes([shape])


def test_drawing_requires_this_owner_frame_and_live_session(native, monkeypatch):
    captured = []
    monkeypatch.setattr(desktop, 'draw', lambda *args: captured.append(args) or {'drawn': True})
    current = frame()
    with pytest.raises(ValueError):
        guidance.draw(current['id'], SHAPES)
    with guidance.turn(current):
        assert guidance.available()
        assert current['id'] in guidance.instructions()
        with pytest.raises(ValueError):
            guidance.draw('another-frame', SHAPES)
        assert guidance.draw(current['id'], SHAPES)['drawn']
        desktop.hidden()
        with pytest.raises(ValueError):
            guidance.draw(current['id'], SHAPES)
    assert len(captured) == 1
    assert not guidance.available()


def test_expired_frame_cannot_guide_current_screen(native):
    current = frame(); current['captured_at'] -= 121
    with guidance.turn(current):
        assert not guidance.available()
        with pytest.raises(ValueError):
            guidance.draw(current['id'], SHAPES)


def test_overlay_confirmation_is_required_and_reports_failure(native):
    current = frame()
    answers = []
    worker = threading.Thread(target=lambda: answers.append(desktop.draw(current, SHAPES, 15)))
    worker.start()
    deadline = time.monotonic()+2
    while not desktop.poll(0) and time.monotonic() < deadline:
        time.sleep(.01)
    command = desktop.poll(0)[0]
    assert not answers  # enqueueing a command is not proof of drawing
    desktop.acknowledge(command['seq'], {'ok': True})
    worker.join(2)
    assert answers == [{'drawn': True, 'frame_id': current['id'], 'shapes': 1, 'expires_seconds': 15}]
    failed = []
    def run():
        try:
            desktop.draw(current, SHAPES, 15)
        except RuntimeError as exc:
            failed.append(str(exc))
    worker = threading.Thread(target=run); worker.start()
    deadline = time.monotonic()+2
    while len(desktop.poll(0)) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    desktop.acknowledge(desktop.poll(0)[-1]['seq'], {'ok': False, 'error': 'renderer unavailable'})
    worker.join(2)
    assert failed == ['renderer unavailable']


def test_native_hotkey_request_is_not_stolen_by_browser_tab(native, monkeypatch):
    from tests.test_look_now import jpeg
    current = frame()
    monkeypatch.setattr(look_now, 'capture_frame', lambda: (jpeg(), current))
    item = look_now.request('hotkey')
    assert look_now.wait(0, timeout=.01) is None
    command = desktop.poll(0)[0]
    assert command['kind'] == 'activate' and command['item']['seq'] == item['seq']
    assert look_now.frame_for(item['seq']) == current
    assert look_now.image_for(item['seq']) == jpeg()
    assert look_now.image_for(item['seq']) is None


def test_second_screen_hotkey_stops_without_another_capture(native, monkeypatch):
    monkeypatch.setattr(look_now, 'capture_frame', lambda: pytest.fail('Stop must not capture'))
    look_now.on_hotkey()
    assert desktop.poll(0)[0]['item']['kind'] == 'stop'


def test_keyboard_callback_returns_while_action_is_blocked(monkeypatch):
    from app.hotkey import GlobalHotkeys
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    bindings = {}
    class Listener:
        def __init__(self, entries): bindings.update(entries)
        def start(self): pass
        def stop(self): stopped.set()
    monkeypatch.setitem(sys.modules, 'pynput', SimpleNamespace(keyboard=SimpleNamespace(GlobalHotKeys=Listener)))
    def capture():
        started.set(); release.wait(3)
    keys = GlobalHotkeys(); keys.bind('<ctrl>+<alt>+c', capture)
    try:
        assert keys.start()
        bindings['<ctrl>+<alt>+c']()
        assert started.wait(1)
        assert not release.is_set()  # the hook returned despite blocked capture
    finally:
        release.set(); keys.stop()
    assert stopped.is_set()


@pytest.mark.parametrize('url', ['https://example.com/companion', 'http://127.0.0.1:7860/board',
                                'http://127.0.0.1:9999/companion'])
def test_bridge_does_not_disclose_token_after_navigation(url):
    ipc = SimpleNamespace(url='http://127.0.0.1:7860')
    bridge = Bridge(ipc, 'master-secret')
    bridge._window = SimpleNamespace(get_current_url=lambda: url)
    with pytest.raises(ValueError):
        bridge.connect()


def test_microphone_permission_is_requested_once_after_invocation(tmp_path):
    prompts = []
    bridge = SimpleNamespace(_activation=False, _trusted=lambda: True, _ipc=SimpleNamespace(url='http://127.0.0.1:7860'),
                             _window=SimpleNamespace(create_confirmation_dialog=lambda *args: prompts.append(args) or True))
    policy = MicrophonePermission(bridge, tmp_path/'permissions.json')
    assert not policy.decide('Microphone') and not prompts
    bridge._activation=True
    assert not policy.decide('Camera') and not prompts
    assert not policy.decide('Microphone', uri='https://foreign.example') and not prompts
    assert policy.decide('Microphone') and len(prompts)==1
    restarted = MicrophonePermission(bridge, tmp_path/'permissions.json')
    assert restarted.decide('Microphone') and len(prompts)==1
    bridge._activation=False
    assert not restarted.decide('Microphone')


def test_native_routes_require_local_owner_and_private_session(native, monkeypatch):
    from dashboard import server
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master-secret')
    with TestClient(server.app, base_url='http://127.0.0.1:7860', client=('127.0.0.1', 123)) as client:
        headers={'Authorization':'Bearer master-secret'}
        assert client.get('/api/companion/desktop/events',headers=headers).status_code==403
        headers['X-Apex-Desktop-Session']='private-desktop-session'
        assert client.get('/api/companion/desktop/events',headers=headers).status_code==200
        bad={**headers,'Origin':'https://foreign.example'}
        assert client.post('/api/companion/desktop',headers=bad,json={'action':'hidden'}).status_code==403
        assert client.post('/api/companion/desktop',headers=headers,json={'action':'hidden'}).status_code==200
        assert not desktop.status()['shown']
    with TestClient(server.app, base_url='http://127.0.0.1:7860', client=('192.0.2.4', 123)) as remote:
        assert remote.get('/api/companion/desktop/events',headers=headers).status_code==403


def test_canvas_uses_screenshot_coordinates_and_literal_labels():
    calls=[]
    class Canvas:
        def create_line(self,*args,**kwargs): calls.append((args,kwargs))
        def create_text(self,*args,**kwargs): calls.append((args,kwargs));return 1
        def bbox(self,item): return None
    paint(Canvas(), {'width':1920,'height':1080},
          [{'type':'arrow','points':[[0,0],[1,1]],'label':'<script>plain label</script>'}])
    assert calls[0][0] == (0,0,1919,1079)
    assert calls[0][1]['arrow']=='last'
    assert calls[1][1]['text']=='<script>plain label</script>'


@pytest.mark.skipif(os.name!='nt',reason='real transparent Windows overlay')
def test_real_windows_overlay_acknowledges_click_through_and_expiry():
    from ctypes import wintypes
    python=Path(sys.executable)
    if python.name.lower()=='pythonw.exe':python=python.with_name('python.exe')
    proc=subprocess.Popen([str(python),'-u','-m','app.screen_overlay'],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,encoding='utf-8')
    replies=queue.Queue()
    reader=threading.Thread(target=lambda:[replies.put(json.loads(line))for line in proc.stdout],daemon=True)
    reader.start()
    user=ctypes.windll.user32
    user.GetWindowLongPtrW.argtypes=[wintypes.HWND,ctypes.c_int]
    user.GetWindowLongPtrW.restype=ctypes.c_ssize_t
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    try:
        message={'kind':'draw','seq':1,'seconds':2,'frame':{'left':32,'top':32,'width':420,'height':240},'shapes':SHAPES}
        proc.stdin.write(json.dumps(message)+'\n');proc.stdin.flush()
        result=replies.get(timeout=15)
        assert result['ok'],result
        hwnd=result['hwnd']
        assert user.IsWindowVisible(hwnd)
        assert user.GetWindowLongPtrW(hwnd,-20)&CLICK_THROUGH==CLICK_THROUGH
        time.sleep(2.3)
        assert not user.IsWindowVisible(hwnd),'expired marks must disappear'
        proc.stdin.write('{"kind":"quit"}\n');proc.stdin.flush();proc.wait(timeout=5)
        assert proc.returncode==0,proc.stderr.read()
    finally:
        if proc.poll()is None:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=10)


@pytest.mark.skipif(os.name!='nt',reason='real Windows global key registration')
def test_real_windows_function_key_dispatches_and_releases():
    from app.hotkey import GlobalHotkeys
    invoked=threading.Event()
    keys=GlobalHotkeys();keys.bind('<f24>',invoked.set)
    try:
        assert keys.start()
        ctypes.windll.user32.keybd_event(0x87,0,0,0)
        ctypes.windll.user32.keybd_event(0x87,0,2,0)
        assert invoked.wait(3),'registered function key must reach the callback'
    finally:
        keys.stop()
    # A second manager can register it: the previous one really released it.
    next_keys=GlobalHotkeys();next_keys.bind('<f24>',lambda:None)
    try:
        assert next_keys.start()
    finally:
        next_keys.stop()


def test_startup_install_restores_previous_resident_profile(monkeypatch):
    from scripts import setup_screen_companion as setup
    store={('Run','ApexResident'):(r'"C:\Apex\pythonw.exe" "C:\Apex\main.py" --resident',1)}
    class Key:
        def __init__(self,path):self.path='Run' if path.endswith('\\Run') else 'Backup'
        def __enter__(self):return self
        def __exit__(self,*args):pass
    def query(key,name):
        if (key.path,name) not in store:raise FileNotFoundError(name)
        return store[(key.path,name)]
    registry=SimpleNamespace(HKEY_CURRENT_USER=1,KEY_SET_VALUE=2,KEY_QUERY_VALUE=4,REG_SZ=1,
       CreateKeyEx=lambda root,path,*args:Key(path),OpenKey=lambda root,path,*args:Key(path),
       QueryValueEx=query,SetValueEx=lambda key,name,unused,kind,value:store.__setitem__((key.path,name),(value,kind)),
       DeleteValue=lambda key,name:store.pop((key.path,name)))
    monkeypatch.setitem(sys.modules,'winreg',registry)
    previous=store[('Run','ApexResident')]
    command=r'"C:\Apex\pythonw.exe" "C:\Apex\scripts\start_screen_companion.py"'
    setup.install(command);setup.install(command)
    assert store[('Run','ApexResident')][0]==command
    assert setup.uninstall()
    assert store[('Run','ApexResident')]==previous
    assert ('Backup','PreviousStartup')not in store
    assert not setup.uninstall(),'removal must not delete the restored unrelated profile'


def test_startup_command_quotes_paths_and_prefers_windowed_python(tmp_path,monkeypatch):
    from scripts import setup_screen_companion as setup
    root=tmp_path/'Apex project';root.mkdir()
    python=root/'python.exe';python.touch();(root/'pythonw.exe').touch()
    monkeypatch.setattr(setup,'ROOT',root)
    command=setup.startup_command(str(python))
    assert command.startswith('"') and 'pythonw.exe"'in command
    assert '"'+str(root/'scripts'/'start_screen_companion.py')+'"'in command


def test_startup_keeps_explicit_wake_settings_and_enables_native_window(tmp_path,monkeypatch):
    from scripts import start_screen_companion as startup
    monkeypatch.setattr(startup,'ROOT',tmp_path)
    (tmp_path/'.env').write_text('CELINE_WAKE_ENABLED=true\nWAKE_WORD_ENABLED=false\n',encoding='utf-8')
    monkeypatch.delenv('CELINE_WAKE_ENABLED',raising=False)
    monkeypatch.delenv('WAKE_WORD_ENABLED',raising=False)
    result=startup.environment()
    assert result['COMPANION_DESKTOP_ENABLED']=='true'
    assert result['CELINE_WAKE_ENABLED']=='true'
    assert result['WAKE_WORD_ENABLED']=='false'


def test_gpu_warmup_does_not_delay_resident_start(tmp_path,monkeypatch):
    import io
    from scripts import start_screen_companion as startup, run_apex_qwen as qwen_launcher
    qwen=tmp_path/'apex-qwen-fast-env'/'Scripts'/'python.exe';qwen.parent.mkdir(parents=True);qwen.touch()
    monkeypatch.setattr(startup.Path,'home',staticmethod(lambda:tmp_path))
    monkeypatch.setattr(qwen_launcher,'port_in_use',lambda *args:False)
    monkeypatch.setattr(startup.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    called=[];terminated=[]
    voice=SimpleNamespace(poll=lambda:None)
    resident=SimpleNamespace(poll=lambda:0,returncode=0)
    def spawn(argv,**kwargs):
        called.append((argv,kwargs));return voice if len(called)==1 else resident
    monkeypatch.setattr(startup.subprocess,'Popen',spawn)
    monkeypatch.setattr(qwen_launcher,'stop',lambda proc:terminated.append(proc))
    assert startup.supervise({'COMPANION_DESKTOP_ENABLED':'true'},io.StringIO())==0
    assert len(called)==2 and called[1][0][-1]=='--resident'
    assert called[1][1]['env']['VOICEBOX_URL']=='http://127.0.0.1:17494'
    assert terminated==[resident,voice]


def test_expired_native_capture_cannot_be_reused(native):
    from tests.test_look_now import jpeg
    item=look_now.request('hotkey',image=jpeg(),now=time.time()-121)
    assert look_now.image_for(item['seq'])is None


def test_model_only_receives_drawing_for_an_owner_host_frame(core_agent,native,monkeypatch):
    from agent import telemetry
    from tests.test_companion import jpeg
    captured=[]
    monkeypatch.setattr(core_agent,'_all_tools',lambda:[{'name':'screen_draw'},{'name':'recall'}])
    def create(*args,**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type='text',text='A short explanation.')],stop_reason='end_turn')
    monkeypatch.setattr(telemetry,'create',create)
    current=frame()
    with guidance.turn(current):
        core_agent.run('Explain this',companion_mode='discuss',channel_id='companion:1',
                       screen_origin='host',screen_image=jpeg(),by_owner=True,withhold=frozenset())
        assert {t['name']for t in captured[-1]['tools']}=={'screen_draw','recall'}
        core_agent.run('Shared elsewhere',companion_mode='discuss',channel_id='companion:2',
                       screen_origin='browser',by_owner=True,withhold=frozenset())
        assert {t['name']for t in captured[-1]['tools']}=={'recall'}
    core_agent.run('Ordinary chat',channel_id='companion:3',include_screenshot=False,by_owner=True,withhold=frozenset())
    assert {t['name']for t in captured[-1]['tools']}=={'recall'}


@pytest.mark.skipif(os.name!='nt',reason='real native WebView2 window')
def test_real_native_window_authenticates_boots_silently_and_exits(tmp_path,monkeypatch):
    import socket
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from dashboard.companion import router
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','')
    monkeypatch.setenv('DASHBOARD_TOKEN','')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    app=FastAPI();app.include_router(router)
    app.mount('/static',StaticFiles(directory=Path(__file__).resolve().parents[1]/'dashboard'/'static'),name='static')
    @app.get('/api/status')
    def ready():return {'agent_ready':True,'voices':{}}
    @app.get('/api/voicebox/profiles')
    def profiles():return {'profiles':[]}
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',proxy_headers=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started and time.monotonic()<deadline:time.sleep(.05)
    assert server.started
    try:
        result=desktop.start(f'http://127.0.0.1:{port}')
        assert result['running']
        deadline=time.monotonic()+40
        while not desktop.status()['ready']and time.monotonic()<deadline:
            if not desktop.running():break
            time.sleep(.1)
        log=tmp_path/'Apex'/'Companion'/'desktop.log'
        assert desktop.status()['ready'],log.read_text(errors='replace')if log.exists()else desktop.status()
        assert not desktop.status()['shown']and desktop.status()['mode']=='idle'
        assert desktop.status()['hotkey_ready']
    finally:
        desktop.stop();server.should_exit=True;thread.join(5)
    assert not desktop.running()
