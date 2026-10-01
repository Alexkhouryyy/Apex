"""Regional feed validation, unit preservation, authentication and outage bounds."""
import asyncio
from collections import OrderedDict
import httpx
import pytest
from fastapi.testclient import TestClient
from dashboard import server, world_flights as flights
from dashboard.ratelimit import AuthThrottle

NOW = 1790856000000

def aircraft(**extra):
    return {'hex': 'ABC123', 'type': 'adsb_icao', 'flight': 'MEA123  ', 'r': 'OD-TEST', 't': 'A320',
            'lat': 34.1, 'lon': 35.6, 'seen_pos': 1.5, 'alt_baro': 35000, 'alt_geom': 36000,
            'gs': 440, 'track': 180, **extra}

def feed(records=None, **extra):
    return {'msg': 'No error', 'now': NOW, 'ac': [aircraft()] if records is None else records, **extra}

@pytest.fixture
def rig(monkeypatch):
    import config
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'flight-test')
    monkeypatch.setattr(server, '_throttle', AuthThrottle())
    monkeypatch.setattr(flights, '_cache', OrderedDict())
    monkeypatch.setattr(flights, '_next_request', 0)
    monkeypatch.setattr(flights, '_lock', asyncio.Lock())
    clock = {'ms': NOW, 'mono': 100.0}
    monkeypatch.setattr(flights, 'wall_time', lambda: clock['ms'] / 1000)
    monkeypatch.setattr(flights, 'monotonic', lambda: clock['mono'])
    calls, payload = [], {'data': feed(), 'offline': False}
    def handle(request):
        calls.append(request)
        if payload['offline']: raise httpx.ConnectError('offline', request=request)
        return httpx.Response(200, json=payload['data'])
    original = httpx.AsyncClient
    monkeypatch.setattr(flights.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with TestClient(server.app) as client:
        yield client, {'Authorization': 'Bearer flight-test'}, clock, calls, payload

def test_auth_region_cache_and_credential_isolation(rig):
    client, headers, _, calls, _ = rig
    url = '/api/world/layers/flights?lat=34.12&lng=35.65'
    assert client.get(url).status_code == 401
    data = client.get(url, headers=headers).json()
    assert data['source'] == 'ADSB.lol' and data['area'] == {'lat': 34.1, 'lng': 35.6, 'radius_nm': 250}
    assert data['generated_at'] == data['fetched_at'] == NOW
    assert data['aircraft'][0]['position_at'] == NOW - 1500
    assert not data['stale']
    assert str(calls[0].url) == 'https://api.adsb.lol/v2/lat/34.1/lon/35.6/dist/250'
    assert 'authorization' not in calls[0].headers
    assert 'github.com/Alexkhouryyy/Apex/issues' in calls[0].headers['user-agent']
    assert client.get(url, headers=headers).json() == data and len(calls) == 1
    for query in ('lat=91&lng=0', 'lat=0&lng=181', 'lat=nan&lng=0', 'lat=0', 'lat=inf&lng=0'):
        assert client.get('/api/world/layers/flights?' + query, headers=headers).status_code == 422
    assert len(calls) == 1

def test_outage_retains_original_position_and_feed_times(rig):
    client, headers, clock, calls, payload = rig
    url = '/api/world/layers/flights?lat=34.1&lng=35.6'
    original = client.get(url, headers=headers).json()
    clock['mono'] += 31; clock['ms'] += 31000; payload['offline'] = True
    stale = client.get(url, headers=headers).json()
    assert stale['stale'] and stale['refresh_failed']
    for key in ('aircraft', 'generated_at', 'fetched_at'): assert stale[key] == original[key]
    client.get(url, headers=headers); assert len(calls) == 2
    clock['mono'] += 31; clock['ms'] += 31000; payload['offline'] = False
    payload['data'] = feed(now=clock['ms'])
    assert not client.get(url, headers=headers).json()['stale']

def test_negative_cache_and_new_area_throttle(rig):
    client, headers, clock, calls, payload = rig
    payload['offline'] = True
    url = '/api/world/layers/flights?lat=34.1&lng=35.6'
    for _ in range(3): assert client.get(url, headers=headers).status_code == 503
    assert len(calls) == 1
    assert client.get('/api/world/layers/flights?lat=1&lng=1', headers=headers).status_code == 503
    assert len(calls) == 1
    for i in range(20):
        clock['mono'] += 6
        client.get(f'/api/world/layers/flights?lat={i}&lng=1', headers=headers)
    assert len(flights._cache) == 16

def test_validation_preserves_units_ground_and_unknowns():
    records = [aircraft(), aircraft(seen_pos=0.5), aircraft(hex='bad-id'), aircraft(hex='bbbbbb', seen_pos=121),
               aircraft(hex='cccccc', lat=True), aircraft(hex='dddddd', lon=181), aircraft(hex='aa0000', lat=-34.1),
               aircraft(hex='eeeeee', alt_baro='ground', alt_geom=None, gs=None, track=None),
               aircraft(hex='ffffff', alt_geom=None, gs=-1, track=999), None]
    data = flights._snapshot(feed(records), NOW, (34.1, 35.6))
    assert len(data['aircraft']) == 3
    byid = {a['id']: a for a in data['aircraft']}
    a = byid['abc123']; assert a['position_at'] == NOW - 500 and a['callsign'] == 'MEA123'
    assert a['altitude_ft'] == 36000 and a['altitude_kind'] == 'geometric' and a['speed_knots'] == 440
    assert byid['eeeeee']['on_ground'] and byid['eeeeee']['altitude_ft'] is None
    assert byid['ffffff']['altitude_kind'] == 'barometric' and byid['ffffff']['altitude_ft'] == 35000
    assert byid['ffffff']['speed_knots'] is None and byid['ffffff']['track_deg'] is None

def test_empty_aged_and_invalid_snapshots(rig):
    client, headers, clock, _, payload = rig
    assert flights._snapshot(feed([]), NOW, (0, 0))['aircraft'] == []
    for data in (None, {}, feed(now=True), feed(now=NOW + 30001), feed(msg='error'), feed(ac=None)):
        with pytest.raises(ValueError): flights._snapshot(data, NOW, (0, 0))
    clock['ms'] += 100000
    assert client.get('/api/world/layers/flights?lat=0&lng=0', headers=headers).json()['stale']

def test_record_and_stream_bounds(rig, monkeypatch):
    data = flights._snapshot(feed([aircraft(hex=f'{i:06x}') for i in range(550)]), NOW, (34.1, 35.6))
    assert data['truncated'] and len(data['aircraft']) == 500
    monkeypatch.setattr(flights, '_MAX_BYTES', 20)
    client, headers, _, _, _ = rig
    assert client.get('/api/world/layers/flights?lat=0&lng=0', headers=headers).status_code == 503


def test_provider_rate_limit_applies_across_regions(rig, monkeypatch):
    client, headers, clock, calls, payload = rig
    real_client = httpx._client.AsyncClient
    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '180'})
    monkeypatch.setattr(flights.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    assert client.get('/api/world/layers/flights?lat=0&lng=0', headers=headers).status_code == 503
    clock['mono'] += 40
    response = client.get('/api/world/layers/flights?lat=1&lng=1', headers=headers)
    assert response.status_code == 503 and response.headers['retry-after'] == '140'
    assert len(calls) == 1
