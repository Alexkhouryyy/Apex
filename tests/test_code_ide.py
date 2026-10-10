"""Owner access, real Git checkpoints, private proxy credentials and IDE lifecycle."""
import json
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config
from agent import access_tokens, code_ide, code_studio as studio
from dashboard import code_ide as gateway, server
from tests.test_code_studio import lab, git

MASTER = 'ide-test-master'


@pytest.fixture
def client(test_db, monkeypatch):
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', MASTER)
    server._throttle.reset('testclient')
    access_tokens.init_db()
    monkeypatch.setattr(gateway, '_grants', {})
    return TestClient(server.app)


@pytest.fixture
def runtime(monkeypatch):
    runtime = SimpleNamespace(secret='private-engine-secret', connection_token='private-engine-cookie',
                              port=33999, running=True)
    monkeypatch.setattr(code_ide, 'get', lambda sid: runtime if sid == 17 else None)
    monkeypatch.setattr(code_ide, 'start', lambda sid: {'url':f'/code/ide/{sid}/','running':True})
    return runtime


def launch(client):
    response = client.post('/api/code/sessions/17/ide', json={}, headers={'Authorization':f'Bearer {MASTER}'})
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize('operation', ['ide', 'ide-checkpoint'])
@pytest.mark.parametrize('caller', ['anonymous', 'paired', 'foreign-origin'])
def test_only_master_can_start_or_checkpoint(client, monkeypatch, operation, caller):
    call = Mock()
    monkeypatch.setattr(code_ide, 'start' if operation == 'ide' else 'checkpoint', call)
    headers = {}
    if caller == 'paired':
        headers['Authorization'] = 'Bearer ' + access_tokens.issue('Phone')
    elif caller == 'foreign-origin':
        headers = {'Authorization':f'Bearer {MASTER}', 'Origin':'https://another-site.example'}
    response = client.post(f'/api/code/sessions/17/{operation}', json={}, headers=headers)
    assert response.status_code in (401, 403)
    call.assert_not_called()


def test_cookie_is_scoped_and_cannot_authorize_other_apex_routes(client, runtime):
    response = launch(client)
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/code/ide/17/' in cookie
    assert client.get('/api/code/sessions/17').status_code == 401
    assert client.get('/code/ide/18/').status_code == 401
    assert MASTER not in response.text and runtime.secret not in response.text


@pytest.mark.parametrize('revoke', ['rotate-master', 'expire', 'restart'])
def test_revoked_cookies_cannot_reach_ide(client, runtime, monkeypatch, revoke):
    launch(client)
    key = client.cookies.get(gateway.COOKIE)
    grant = gateway._grants[key]
    if revoke == 'rotate-master':
        monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'rotated-master')
    elif revoke == 'expire':
        gateway._grants[key] = (time.monotonic()-1, *grant[1:])
    else:
        runtime.secret = 'different-generation'
    assert client.get('/code/ide/17/').status_code == 401


def test_http_gateway_forwards_only_private_credentials(client, runtime, monkeypatch):
    launch(client)
    original = httpx.AsyncClient
    async def upstream(request):
        assert request.headers['x-apex-ide-secret'] == runtime.secret
        assert request.headers['cookie'] == 'theia-connection-token='+runtime.connection_token
        assert 'authorization' not in request.headers
        assert request.headers['origin'] == f'http://127.0.0.1:{runtime.port}'
        assert request.url.host == '127.0.0.1' and request.url.port == runtime.port
        assert request.url.path == '/asset.js' and request.url.query == b'v=1'
        return httpx.Response(200, content=b'asset bytes', headers={'set-cookie':'unwanted=1; Path=/'})
    monkeypatch.setattr(gateway.httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(upstream), **kwargs))
    response = client.get('/code/ide/17/asset.js?v=1', headers={'Origin':'http://testserver',
        'Authorization':f'Bearer {MASTER}', 'x-apex-ide-secret':'attacker-controlled'})
    assert response.status_code == 200 and response.content == b'asset bytes'
    assert 'set-cookie' not in response.headers
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/code/ide/17/asset.js', headers={'Origin':'https://foreign.example'}).status_code == 403


@pytest.mark.parametrize('origin', [None, 'https://foreign.example'])
def test_websocket_requires_same_origin_and_grant(client, runtime, origin):
    launch(client)
    headers = {'Origin':origin} if origin else {}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/code/ide/17/socket.io/?EIO=4&transport=websocket', headers=headers):
            pytest.fail('Untrusted socket was accepted')


def test_direct_ide_disk_edits_get_undo_checkpoint_and_keep(lab):
    sid = studio.open_workspace(lab.pid)['id']
    folder = Path(studio.session(sid)['worktree'])
    (folder/'README.md').write_text('saved by the full IDE\n')
    result = code_ide.checkpoint(sid)
    assert result['saved'] and result['warning'] is None
    assert (lab.root/'README.md').read_text() == 'hello\n'
    studio.undo(sid)
    assert (folder/'README.md').read_text() == 'hello\n'
    (folder/'README.md').write_text('final IDE edit\n')
    code_ide.checkpoint(sid)
    studio.keep(sid)
    assert (lab.root/'README.md').read_text() == 'final IDE edit\n'
    assert git(lab.root, 'status','--porcelain') == ''


def test_checkpoint_refuses_busy_session(lab):
    sid = studio.open_workspace(lab.pid)['id']
    with studio._lock:
        studio._operations[sid] = 'agent-turn'
    try:
        with pytest.raises(studio.CodeError, match='busy'):
            code_ide.checkpoint(sid)
    finally:
        with studio._lock:
            studio._operations.pop(sid, None)


@pytest.mark.parametrize('action', ['keep', 'discard'])
def test_session_end_closes_ide_before_removing_worktree(lab, monkeypatch, action):
    sid = studio.open_workspace(lab.pid)['id']
    s = studio.session(sid)
    if action == 'keep':
        (Path(s['worktree'])/'README.md').write_text('changed\n')
    closed = []
    def stop(session_id):
        assert session_id == sid and Path(s['worktree']).is_dir()
        closed.append(True)
    monkeypatch.setattr(code_ide, 'stop', stop)
    getattr(studio, action)(sid)
    assert closed == [True] and not Path(s['worktree']).exists()


def test_archive_installer_refuses_traversal(tmp_path):
    import shutil
    import subprocess
    import zipfile
    app = Path(__file__).resolve().parents[1]/'ide'
    if not shutil.which('node') or not (app/'node_modules/@xhmikosr/decompress').exists():
        pytest.skip('Built IDE dependencies are required for the extractor regression')
    archive = tmp_path/'escape.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escaped.txt', 'unsafe')
        z.writestr('safe.txt', 'safe')
    script = 'require(process.argv[1])(process.argv[2],process.argv[3]).then(()=>process.exit(3)).catch(()=>process.exit(0))'
    result = subprocess.run(['node','-e',script,str(app/'archive-adapter'),str(archive),str(tmp_path/'extracted')],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path/'escaped.txt').exists()


def test_real_ide_private_http_socket_and_cleanup(lab, monkeypatch):
    import os
    if os.getenv('APEX_IDE_INTEGRATION') != '1':
        pytest.skip('Enable APEX_IDE_INTEGRATION=1 after building the IDE')
    assert code_ide.installation()['installed'], 'Build the IDE and download its extensions first'
    sid = studio.open_workspace(lab.pid)['id']
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', MASTER)
    server._throttle.reset('testclient')
    with TestClient(server.app) as client:
        response = client.post(f'/api/code/sessions/{sid}/ide',json={},headers={'Authorization':f'Bearer {MASTER}'})
        assert response.status_code == 200, response.text
        runtime = code_ide.get(sid)
        process = runtime.process
        try:
            private = f'http://127.0.0.1:{runtime.port}'
            with httpx.Client(trust_env=False, timeout=10) as direct:
                assert direct.get(private+'/').status_code == 401
                assert direct.get(private+'/socket.io/?EIO=4&transport=polling').status_code == 403
            assert client.get(f'/code/ide/{sid}/').status_code == 200
            assert client.get(f'/code/ide/{sid}/webview/index.html').status_code == 403
            with client.websocket_connect(f'/code/ide/{sid}/socket.io/?EIO=4&transport=websocket',
                                         headers={'Origin':'http://testserver'}) as socket:
                assert socket.receive_text().startswith('0{"sid"')
            old_cookie = client.cookies.get(gateway.COOKIE)
            old_secret, old_gateway, old_log = runtime.secret, runtime.webviews, runtime.log
            process.terminate()
            process.wait(timeout=10)
            response = client.post(f'/api/code/sessions/{sid}/ide',json={},headers={'Authorization':f'Bearer {MASTER}'})
            assert response.status_code == 200, response.text
            assert runtime.secret != old_secret and old_log.closed
            assert not old_gateway.thread.is_alive()
            assert client.get(f'/code/ide/{sid}/',headers={'Cookie':f'{gateway.COOKIE}={old_cookie}'}).status_code == 401
            assert client.get(f'/code/ide/{sid}/').status_code == 200
            process = runtime.process
            (Path(studio.session(sid)['worktree'])/'README.md').write_text('real IDE lifecycle\n')
            code_ide.checkpoint(sid)
            studio.keep(sid)
            assert process.poll() is not None
            assert code_ide.get(sid) is None and runtime.webviews is None
        finally:
            code_ide.stop(sid)
