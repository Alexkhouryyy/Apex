"""The live photoreal face (Simli): Apex's session route.

A fake Simli API runs on a real local port. The route must send the key only
to Simli, in its header; ask for a capped session for the configured face;
hand the page the session token and ICE servers but never the key; and turn
every failure into a plain reason the page can show.
"""
import socket
import threading
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

KEY = 'sk-simli-test-key-0123456789'


@pytest.fixture
def simli():
    """A stand-in for api.simli.ai that records what it was sent."""
    import uvicorn
    seen, mode = [], {'token': 200, 'ice': 200}
    app = FastAPI()

    @app.post('/compose/token')
    async def token(request: Request):
        seen.append(('token', dict(request.headers), await request.json()))
        if mode['token'] != 200:
            return __import__('fastapi').responses.JSONResponse({'detail': 'no'}, status_code=mode['token'])
        return {'session_token': 'sess-abc'}

    @app.get('/compose/ice')
    async def ice(request: Request):
        seen.append(('ice', dict(request.headers), None))
        if mode['ice'] != 200:
            return __import__('fastapi').responses.JSONResponse({'detail': 'no'}, status_code=500)
        return [{'urls': ['turn:turn.example:3478'], 'username': 'u', 'credential': 'c'}]

    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(0.05)
    yield f'http://127.0.0.1:{port}', seen, mode
    server.should_exit = True; thread.join(5)


@pytest.fixture
def apex(monkeypatch):
    import config
    from dashboard import companion
    app = FastAPI(); app.include_router(companion.router)
    monkeypatch.setattr(config, 'SIMLI_API_KEY', KEY)
    monkeypatch.setattr(config, 'SIMLI_FACE_ID', 'face-123')
    monkeypatch.setattr(config, 'SIMLI_MODEL', '')
    return TestClient(app), config


def test_session_sends_the_key_only_to_simli_and_returns_only_the_token(apex, simli, monkeypatch):
    client, config = apex
    url, seen, _ = simli
    monkeypatch.setattr(config, 'SIMLI_URL', url)
    r = client.post('/api/avatar/live/session')
    data = r.json()
    assert data == {'available': True, 'session_token': 'sess-abc',
                    'ice_servers': [{'urls': ['turn:turn.example:3478'], 'username': 'u', 'credential': 'c'}]}
    assert KEY not in r.text
    kind, headers, body = seen[0]
    assert kind == 'token' and headers['x-simli-api-key'] == KEY
    assert body == {'faceId': 'face-123', 'handleSilence': True,
                    'maxSessionLength': config.SIMLI_MAX_SESSION, 'maxIdleTime': config.SIMLI_MAX_IDLE}
    monkeypatch.setattr(config, 'SIMLI_MODEL', 'fasttalk')
    client.post('/api/avatar/live/session')
    assert [b for k, _, b in seen if k == 'token'][-1]['model'] == 'fasttalk'


def test_not_set_up_says_what_to_add(apex, monkeypatch):
    client, config = apex
    monkeypatch.setattr(config, 'SIMLI_API_KEY', '')
    data = client.post('/api/avatar/live/session').json()
    assert data['available'] is False and 'SIMLI_API_KEY' in data['reason']


@pytest.mark.parametrize('status,words', [(401, 'refused the API key'), (402, 'SIMLI_FACE_ID')])
def test_refusals_become_reasons(apex, simli, monkeypatch, status, words):
    client, config = apex
    url, _, mode = simli
    monkeypatch.setattr(config, 'SIMLI_URL', url)
    mode['token'] = status
    data = client.post('/api/avatar/live/session').json()
    assert data['available'] is False and words in data['reason'] and KEY not in str(data)


def test_unreachable_and_ice_failures(apex, simli, monkeypatch):
    client, config = apex
    url, _, mode = simli
    monkeypatch.setattr(config, 'SIMLI_URL', url)
    mode['ice'] = 500
    data = client.post('/api/avatar/live/session').json()
    assert data['available'] and data['ice_servers'] == [{'urls': ['stun:stun.l.google.com:19302']}]
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); closed = s.getsockname()[1]
    monkeypatch.setattr(config, 'SIMLI_URL', f'http://127.0.0.1:{closed}')
    data = client.post('/api/avatar/live/session').json()
    assert data['available'] is False and 'could not be reached' in data['reason']


def test_other_sites_cannot_open_a_paid_session(apex):
    client, _ = apex
    assert client.post('/api/avatar/live/session', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_bundle_is_vendored_and_pinned():
    from pathlib import Path
    import hashlib
    root = Path(__file__).resolve().parents[1] / 'dashboard' / 'static' / 'vendor' / 'simli'
    readme = (root / 'README.md').read_text()
    digest = hashlib.sha256((root / 'simli.bundle.js').read_bytes()).hexdigest()
    assert digest in readme, 'the bundle changed without its README being updated'
    assert 'simli-client' in (root / 'package-lock.json').read_text()
