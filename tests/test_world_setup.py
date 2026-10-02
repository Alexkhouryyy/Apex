import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard import server, world_setup

OWNER = {'Authorization': 'Bearer setup-owner', 'Origin': 'http://testserver'}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'setup-owner')
    server._throttle.reset('testclient')
    monkeypatch.setattr(world_setup, 'ENGINE', tmp_path)
    (tmp_path / 'APEX_UPSTREAM.json').write_text(json.dumps({'revision': 'a' * 40}))
    monkeypatch.setattr(world_setup, '_update_cache', None)
    monkeypatch.setattr(world_setup, '_update_lock', asyncio.Lock())
    return TestClient(server.app)


def fake_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(world_setup.httpx, 'AsyncClient', lambda **kw:
                        real(transport=httpx.MockTransport(handler), **kw))


def test_setup_shell_public_but_diagnostics_and_updates_owner_only(client, monkeypatch):
    assert client.get('/setup').status_code == 200
    assert client.get('/api/setup/status').status_code == 401
    monkeypatch.setattr('agent.access_tokens.verify', lambda token: token == 'paired-device')
    assert client.get('/api/setup/status', headers={'Authorization': 'Bearer paired-device'}).status_code == 403
    assert client.post('/api/setup/world-update', headers={'Authorization': 'Bearer paired-device',
                       'Origin': 'http://testserver'}).status_code == 403
    assert client.post('/api/setup/world-update', headers={**OWNER, 'Origin': 'https://foreign.test'}).status_code == 403


@pytest.mark.parametrize('latest, state', [('a' * 40, 'current'), ('b' * 40, 'update_available')])
def test_update_check_is_bounded_read_only_and_cached(client, monkeypatch, latest, state):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == world_setup.UPSTREAM
        assert request.method == 'GET'
        assert 'authorization' not in request.headers
        assert 'cookie' not in request.headers
        return httpx.Response(200, json={'sha': latest})
    fake_http(monkeypatch, handler)
    first = client.post('/api/setup/world-update', headers=OWNER)
    assert first.status_code == 200
    assert first.json()['state'] == state and first.json()['automatic_install'] is False
    assert client.post('/api/setup/world-update', headers=OWNER).json() == first.json()
    assert len(calls) == 1


@pytest.mark.parametrize('payload, code', [({'sha': 'bad'}, 200), ({'message': 'rate limited'}, 429)])
def test_update_failure_does_not_claim_current_or_replace_previous_check(client, monkeypatch, payload, code):
    fake_http(monkeypatch, lambda request: httpx.Response(code, json=payload))
    data = client.post('/api/setup/world-update', headers=OWNER).json()
    assert data['state'] == 'unavailable' and data['installed'] == 'a' * 40
    assert world_setup._update_cache is None


def test_diagnostics_use_local_voice_only_and_return_no_credentials(client, monkeypatch):
    import config
    from agent import apps
    monkeypatch.setattr(config, 'VOICEBOX_URL', 'http://127.0.0.1:17494')
    monkeypatch.setattr(config, 'VOICEBOX_PROFILE', 'my-celine')
    monkeypatch.setattr(config, 'AGENT_MODEL', 'claude-test')
    monkeypatch.setattr(config, 'ANTHROPIC_API_KEY', 'private-api-key-do-not-render')
    monkeypatch.setattr(server, '_agent_ref', None)
    monkeypatch.setattr(apps, '_key', lambda: 'private-composio-key-do-not-render')
    monkeypatch.setattr(world_setup.runtime, 'status', lambda: {'installed': True, 'revision': 'a' * 40})
    def handler(request):
        assert request.method == 'GET' and request.url.host == '127.0.0.1'
        if request.url.path == '/health': return httpx.Response(200, json={'streaming': True})
        assert request.url.path == '/profiles'
        return httpx.Response(200, json=[{'id': 'my-celine', 'name': 'Celine', 'voice_type': 'cloned'}])
    fake_http(monkeypatch, handler)
    r = client.get('/api/setup/status', headers=OWNER)
    assert r.status_code == 200 and 'private-' not in r.text
    data = r.json()
    assert data['voice']['state'] == 'ready' and data['voice']['audio_verified'] is False
    assert data['voice']['profile_id'] == 'my-celine'
    assert data['model']['configured'] is True and data['model']['verified'] is False
    assert data['apps']['configured'] is True and data['apps']['authorization_required'] is True


def test_local_voice_failure_is_visible(client, monkeypatch):
    monkeypatch.setattr(world_setup.runtime, 'status', lambda: {})
    fake_http(monkeypatch, lambda request: httpx.Response(503))
    data = client.get('/api/setup/status', headers=OWNER).json()
    assert data['voice']['state'] == 'unavailable'
