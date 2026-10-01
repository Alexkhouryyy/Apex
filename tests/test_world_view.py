"""World View search boundaries; no live provider calls."""
import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from dashboard import world
from dashboard import server
from dashboard.ratelimit import AuthThrottle
from dashboard.server import app


@pytest.fixture
def client(monkeypatch):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'world-test-token')
    monkeypatch.setattr(server, '_throttle', AuthThrottle())
    world._cache.clear()
    world._last_search = 0
    world._search_lock = asyncio.Lock()
    return TestClient(app)


def test_shell_is_public_but_search_requires_auth(client):
    response = client.get('/world')
    assert response.status_code == 200
    assert 'World View' in response.text
    assert client.get('/api/world/search?q=Byblos').status_code == 401
    assert client.get('/world/private').status_code == 401


def test_search_validates_input_before_provider(client):
    headers = {'Authorization': 'Bearer world-test-token'}
    for query in ('a', 'x' * 101, '   ', 'Ro\x00me'):
        assert client.get('/api/world/search', params={'q': query}, headers=headers).status_code in (400, 422)


def test_search_filters_provider_records_and_caches(client, monkeypatch):
    calls = []
    payload = {'features': [
        {'geometry': {'type': 'Point', 'coordinates': [35.65, 34.12]},
         'properties': {'name': '<script>Byblos</script>', 'city': '<script>Byblos</script>', 'country': 'Lebanon'}},
        {'geometry': {'type': 'Point', 'coordinates': [999, 34]}, 'properties': {'name': 'Bad'}},
        {'geometry': {'type': 'Point', 'coordinates': [True, 34]}, 'properties': {'name': 'Bad'}},
        {'geometry': {'type': 'LineString', 'coordinates': [35, 34]}, 'properties': {'name': 'Bad'}},
        None,
    ]}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {'timeout': 8, 'follow_redirects': False}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(200, json=payload, request=httpx.Request('GET', url))

    monkeypatch.setattr(world.httpx, 'AsyncClient', Client)
    headers = {'Authorization': 'Bearer world-test-token'}
    r = client.get('/api/world/search?q=Byblos', headers=headers)
    assert r.status_code == 200
    assert r.json()['results'] == [{'label': '<script>Byblos</script>, Lebanon', 'lat': 34.12, 'lng': 35.65}]
    assert client.get('/api/world/search?q=byblos', headers=headers).json() == r.json()
    assert len(calls) == 1
    assert calls[0][0] == 'https://photon.komoot.io/api/'
    assert 'Authorization' not in calls[0][1]['headers']
    assert client.get('/api/world/search?q=Beirut', headers=headers).status_code == 429


def test_provider_failure_is_recoverable(client, monkeypatch):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): raise httpx.ConnectError('provider offline')
    monkeypatch.setattr(world.httpx, 'AsyncClient', Client)
    response = client.get('/api/world/search?q=Rome', headers={'Authorization': 'Bearer world-test-token'})
    assert response.status_code == 503
    assert 'coordinates' in response.json()['detail']
    assert not world._cache


def test_invalid_provider_shape(client, monkeypatch):
    assert world._places({'features': 'not a list'}) == []
    assert world._places({'features': [{'geometry': None}]}) == []
