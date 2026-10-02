import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard import server, world_engine, world_assistant

ORIGIN = {'Origin': 'http://testserver'}
OWNER = {'Authorization': 'Bearer world-owner', **ORIGIN}


@pytest.fixture
def client(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'world-owner')
    from dashboard.ratelimit import AuthThrottle
    monkeypatch.setattr(server, '_throttle', AuthThrottle())   # earlier tests' failed logins must not lock these out
    world_engine._sessions.clear()
    world_assistant._turns.clear()
    world_assistant._lock = asyncio.Lock()
    monkeypatch.setattr(world_assistant, 'ENGINE', tmp_path)
    (tmp_path / 'APEX_ACTIONS.json').write_text(json.dumps([
        {'name':'set_layer_visibility', 'parameters':{'type':'object', 'additionalProperties':False,
            'required':['layerId','enabled'], 'properties':{'layerId':{'type':'string'}, 'enabled':{'type':'boolean'}}}}
    ]))
    return TestClient(server.app)


def session(client, headers=OWNER):
    response = client.post('/api/world/engine/session', headers=headers)
    assert response.status_code == 200
    return response


def test_public_shell_does_not_start_backend(client, monkeypatch):
    monkeypatch.setattr(world_engine.runtime, 'start', lambda: pytest.fail('shell must not start the engine'))
    assert client.get('/world').status_code == 200
    assert client.get('/world/basic').status_code == 200
    assert client.get('/world/engine/').status_code == 401
    assert client.get('/api/world/engine/status').status_code == 401
    assert client.post('/api/world/engine/session', headers=ORIGIN).status_code == 401


def test_cookie_is_scoped_and_owner_token_never_returned(client):
    response = session(client)
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/world/engine/' in cookie
    assert 'world-owner' not in cookie and 'world-owner' not in response.text
    assert client.get('/api/world/engine/status').status_code == 401


def test_shared_link_navigation_can_sign_in_without_exposing_engine(client,monkeypatch):
    monkeypatch.setattr(world_engine.runtime,'start',lambda:pytest.fail('unauthorized navigation started engine'))
    response=client.get('/world/engine/?setup=1',headers={'Sec-Fetch-Dest':'document'})
    assert response.status_code == 200 and 'world-engine-shell.js' in response.text
    assert client.get('/world/engine/api/setup/status',headers={'Sec-Fetch-Dest':'document'}).status_code == 401


def test_replaced_owner_token_revokes_engine_cookie(client, monkeypatch):
    session(client)
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'replacement-owner')
    monkeypatch.setattr('agent.access_tokens.verify', lambda value: False)
    assert client.get('/world/engine/').status_code == 401


def test_cross_site_session_and_engine_writes_rejected(client):
    assert client.post('/api/world/engine/session', headers={**OWNER,'Origin':'https://foreign.test'}).status_code == 403
    session(client)
    assert client.post('/world/engine/api/setup/keys', json={}, headers={'Origin':'https://foreign.test'}).status_code == 403
    assert client.post('/world/engine/api/setup/keys', json={}).status_code == 403


@pytest.mark.parametrize('path', ['.env','@fs/C:/Users/alexk/Apex/.env','secret.pem','.git/config','ENVIRONMENT'])
def test_private_files_never_reach_backend(client, monkeypatch, path):
    session(client)
    monkeypatch.setattr(world_engine.runtime, 'start', lambda: pytest.fail('private file reached engine'))
    assert client.get('/world/engine/' + path).status_code == 404


def test_device_cannot_change_provider_keys(client, monkeypatch):
    monkeypatch.setattr('agent.access_tokens.verify', lambda value: value == 'device')
    session(client, {'Authorization':'Bearer device', **ORIGIN})
    monkeypatch.setattr(world_engine.runtime, 'start', lambda: pytest.fail('device may not change keys'))
    assert client.post('/world/engine/api/setup/keys', headers=ORIGIN, json={}).status_code == 403


def test_gateway_strips_credentials_and_preserves_stream(client, monkeypatch):
    session(client)
    monkeypatch.setattr(world_engine.runtime, 'start', lambda: 4174)
    requests = []
    class FakeClient:
        def __init__(self, **kwargs): assert kwargs['trust_env'] is False
        def build_request(self, method, url, **kwargs):
            requests.append((str(url),kwargs));return httpx.Request(method,url,**kwargs)
        async def send(self, request, **kwargs):
            return httpx.Response(200, content=b'{"real":"payload"}', headers={'content-type':'application/json'}, request=request)
        async def aclose(self): pass
    monkeypatch.setattr(world_engine.httpx, 'AsyncClient', FakeClient)
    response = client.get('/world/engine/api/earthquakes?max=5', headers={**OWNER,'X-Forwarded-For':'foreign'})
    assert response.status_code == 200 and response.json() == {'real':'payload'}
    url, args = requests[0]
    assert url == 'http://127.0.0.1:4174/world/engine/api/earthquakes?max=5'
    assert not any(key in args['headers'] for key in ('authorization','cookie','x-forwarded-for'))
    assert args['headers']['x-apex-engine-secret'] == world_engine.runtime.secret
    assert response.headers['x-frame-options'] == 'SAMEORIGIN'


def test_start_failure_is_explicit_and_recoverable(client, monkeypatch):
    session(client)
    def fail(): raise RuntimeError('Missing dependencies; run Setup-Apex-World.cmd.')
    monkeypatch.setattr(world_engine.runtime,'start',fail)
    response = client.get('/world/engine/')
    assert response.status_code == 503 and 'Setup-Apex-World.cmd' in response.text


def test_celine_waits_for_tool_result_before_confirming(client, monkeypatch):
    session(client)
    calls = []
    def complete(model,messages,tools):
        calls.append(json.loads(json.dumps(messages)))
        if len(calls) == 1:
            return [{'type':'tool_use','id':'tool-1','name':'set_layer_visibility','input':{'layerId':'flights','enabled':True}}]
        assert 'feed unavailable' in messages[-1]['content'][0]['content']
        return [{'type':'text','text':'Flights could not load: feed unavailable.'}]
    monkeypatch.setattr(world_assistant,'_complete',complete)
    first = client.post('/world/engine/apex/assistant', headers=ORIGIN, json={'message':'Show flights','context':{}}).json()
    assert first['done'] is False and first['text'] == ''
    assert len(first['actions']) == 1
    response = client.post('/world/engine/apex/assistant', headers=ORIGIN,
        json={'turn_id':first['turn_id'],'results':[{'id':'tool-1','result':{'ok':False,'error':'feed unavailable'}}]})
    assert response.status_code == 200 and response.json()['done'] is True
    assert 'could not' in response.json()['text']
    assert not world_assistant._turns


def test_celine_rejects_unissued_tool_results(client, monkeypatch):
    session(client)
    monkeypatch.setattr(world_assistant,'_complete',lambda *args:[{'type':'tool_use','id':'issued','name':'set_layer_visibility','input':{'layerId':'flights','enabled':True}}])
    first=client.post('/world/engine/apex/assistant',headers=ORIGIN,json={'message':'Show flights'}).json()
    response=client.post('/world/engine/apex/assistant',headers=ORIGIN,json={'turn_id':first['turn_id'],'results':[{'id':'forged','result':{'ok':True}}]})
    assert response.status_code == 400


@pytest.mark.parametrize('block',[
    {'type':'tool_use','id':'bad','name':'bash','input':{'command':'anything'}},
    {'type':'tool_use','id':'bad','name':'set_layer_visibility','input':{'layerId':'flights','enabled':'yes'}},
])
def test_celine_only_returns_schema_valid_world_actions(client,monkeypatch,block):
    session(client)
    monkeypatch.setattr(world_assistant,'_complete',lambda *args:[block])
    response=client.post('/world/engine/apex/assistant',headers=ORIGIN,json={'message':'Do it'})
    assert response.status_code == 503 and not world_assistant._turns


def test_celine_turn_is_owned_by_its_browser_session(client,monkeypatch):
    session(client)
    monkeypatch.setattr(world_assistant,'_complete',lambda *args:[{'type':'tool_use','id':'issued','name':'set_layer_visibility','input':{'layerId':'flights','enabled':True}}])
    first=client.post('/world/engine/apex/assistant',headers=ORIGIN,json={'message':'Show flights'}).json()
    session(client)
    response=client.post('/world/engine/apex/assistant',headers=ORIGIN,json={'turn_id':first['turn_id'],'results':[{'id':'issued','result':{'ok':True}}]})
    assert response.status_code == 409


@pytest.mark.parametrize('body', [
    '{"message":"look","context":{"value":NaN}}',
    '{"message":"look","context":{"value":Infinity}}',
    '{"message":"look","context":{"value":1e400}}',
    '{"turn_id":[],"results":[]}',
])
def test_malformed_world_input_returns_client_error(client, monkeypatch, body):
    session(client)
    monkeypatch.setattr(world_assistant, '_complete', lambda *args: pytest.fail('malformed input reached model'))
    response = client.post('/world/engine/apex/assistant', headers={**ORIGIN, 'Content-Type':'application/json'}, content=body)
    assert response.status_code == 400 and not world_assistant._turns
