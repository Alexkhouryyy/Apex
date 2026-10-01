"""Feed bounds, cache/outage semantics and credential isolation."""
import asyncio
import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from dashboard import server, world_layers as layers
from dashboard.ratelimit import AuthThrottle

NOW = 1790856000000


def event(identity='us-example', **props):
    return {'type': 'Feature', 'id': identity,
            'geometry': {'type': 'Point', 'coordinates': [35.65, 34.12, 12.3]},
            'properties': {'mag': 4.2, 'type': 'earthquake', 'place': 'Near Byblos',
                           'time': NOW - 600000, 'updated': NOW - 300000,
                           'status': 'reviewed', 'url': 'https://evil.test/', **props}}


def feed(events=None, generated=NOW):
    return {'type': 'FeatureCollection', 'metadata': {'status': 200, 'generated': generated},
            'features': events if events is not None else [event()]}


@pytest.fixture
def rig(monkeypatch):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'world-layers-test')
    monkeypatch.setattr(server, '_throttle', AuthThrottle())
    monkeypatch.setattr(layers, '_cache', None)
    monkeypatch.setattr(layers, '_next_attempt', 0.0)
    monkeypatch.setattr(layers, '_refresh_failed', False)
    monkeypatch.setattr(layers, '_lock', asyncio.Lock())
    clock = {'ms': NOW, 'mono': 100.0}
    monkeypatch.setattr(layers, 'wall_time', lambda: clock['ms'] / 1000)
    monkeypatch.setattr(layers, 'monotonic', lambda: clock['mono'])
    calls, payload = [], {'data': feed(), 'offline': False}
    def handle(request):
        calls.append(request)
        if payload['offline']:
            raise httpx.ConnectError('offline', request=request)
        return httpx.Response(200, json=payload['data'])
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs == {'timeout': 10, 'follow_redirects': False}
        return original(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(layers.httpx, 'AsyncClient', client)
    with TestClient(server.app) as browser:
        yield browser, {'Authorization': 'Bearer world-layers-test'}, clock, calls, payload


def test_auth_and_cache_do_not_forward_credentials(rig):
    client, headers, clock, calls, _ = rig
    assert client.get('/api/world/layers/earthquakes').status_code == 401
    response = client.get('/api/world/layers/earthquakes', headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data['source'] == 'USGS' and data['generated_at'] == NOW and data['fetched_at'] == NOW
    assert not data['stale'] and not data['refresh_failed']
    assert data['events'][0]['url'] == 'https://earthquake.usgs.gov/earthquakes/eventpage/us-example'
    assert str(calls[0].url) == layers._USGS_URL
    assert 'authorization' not in calls[0].headers
    assert client.get('/api/world/layers/earthquakes', headers=headers).json() == data
    assert len(calls) == 1


def test_outage_returns_stale_snapshot_without_changing_timestamps(rig):
    client, headers, clock, calls, payload = rig
    original = client.get('/api/world/layers/earthquakes', headers=headers).json()
    clock['mono'] += 61; clock['ms'] += 61000; payload['offline'] = True
    cached = client.get('/api/world/layers/earthquakes', headers=headers).json()
    assert cached['stale'] and cached['refresh_failed']
    for key in ('events', 'generated_at', 'fetched_at'):
        assert cached[key] == original[key]
    assert client.get('/api/world/layers/earthquakes', headers=headers).json() == cached
    assert len(calls) == 2
    clock['mono'] += 61; clock['ms'] += 61000; payload['offline'] = False
    payload['data'] = feed(generated=clock['ms'])
    restored = client.get('/api/world/layers/earthquakes', headers=headers).json()
    assert not restored['stale'] and not restored['refresh_failed']
    assert restored['fetched_at'] == clock['ms']


def test_first_outage_is_503_and_retry_is_bounded(rig):
    client, headers, _, calls, payload = rig
    payload['offline'] = True
    for _ in range(3):
        response = client.get('/api/world/layers/earthquakes', headers=headers)
        assert response.status_code == 503 and response.headers['retry-after'] == '60'
    assert len(calls) == 1


def test_old_feed_is_stale_even_when_fetched_now(rig):
    client, headers, clock, _, payload = rig
    clock['ms'] += 360000
    data = client.get('/api/world/layers/earthquakes', headers=headers).json()
    assert data['stale'] and not data['refresh_failed']
    assert data['generated_at'] == NOW and data['fetched_at'] == NOW + 360000


def test_bad_records_and_duplicate_updates_are_filtered():
    valid = event(place='<img src=x onerror=alert(1)>')
    bad_point = copy.deepcopy(valid); bad_point['id'] = 'bad'; bad_point['geometry']['coordinates'][0] = 181
    bad_bool = copy.deepcopy(valid); bad_bool['id'] = 'bool'; bad_bool['geometry']['coordinates'][1] = True
    values = [valid, bad_point, bad_bool, None, event('../bad'), event('low', mag=2.4),
              event('nonquake', type='quarry blast'), event('nan', mag=float('nan')),
              event('expired', time=NOW - 86400001), event('future', time=NOW + 300001),
              event('unknown', updated=None), event(updated=NOW - 100000)]
    data = layers._snapshot(feed(values), NOW)
    assert len(data['events']) == 1
    assert data['events'][0]['updated'] == NOW - 100000
    assert data['events'][0]['place'] == 'Near Byblos'


def test_empty_feed_differs_from_invalid_feed():
    assert layers._snapshot(feed([]), NOW)['events'] == []
    for payload in (None, {}, feed(generated=NOW + 300001), feed(generated=True),
                    {**feed(), 'features': None}):
        with pytest.raises(ValueError): layers._snapshot(payload, NOW)


def test_bounded_records_are_latest_first():
    values = [event('us-' + str(i), time=NOW - i * 1000, updated=NOW) for i in range(350)]
    data = layers._snapshot(feed(values), NOW)
    assert data['truncated'] and len(data['events']) == 300
    assert data['events'][0]['id'] == 'us-0' and data['events'][-1]['id'] == 'us-299'


def test_stream_size_is_bounded(rig, monkeypatch):
    client, headers, _, _, payload = rig
    monkeypatch.setattr(layers, '_MAX_BYTES', 50)
    response = client.get('/api/world/layers/earthquakes', headers=headers)
    assert response.status_code == 503
    assert layers._cache is None
