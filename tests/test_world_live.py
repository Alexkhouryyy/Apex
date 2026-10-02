"""Celine's World View context (dashboard/world_live.py): which layers are on
and what is selected, resolved from the three layer modules' own caches,
never from client-supplied metadata. Plus the SGP4 calculation that tells her
where a selected station is right now."""
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from dashboard import server, world_flights, world_layers, world_live as live, world_satellites
from dashboard.ratelimit import AuthThrottle

NOW_MS = lambda: int(time.time() * 1000)


def station_row(epoch_at=None):
    epoch = datetime.fromtimestamp((epoch_at or time.time()), timezone.utc).replace(tzinfo=None).isoformat()
    return {'OBJECT_NAME': 'ISS TEST', 'NORAD_CAT_ID': 25544, 'EPOCH': epoch, 'MEAN_MOTION': 15.5, 'ECCENTRICITY': 0.0005,
            'INCLINATION': 51.6, 'RA_OF_ASC_NODE': 20, 'ARG_OF_PERICENTER': 30, 'MEAN_ANOMALY': 40, 'EPHEMERIS_TYPE': 0,
            'CLASSIFICATION_TYPE': 'U', 'BSTAR': 0.00001, 'MEAN_MOTION_DOT': 0.0001, 'MEAN_MOTION_DDOT': 0}


@pytest.fixture
def caches(monkeypatch, tmp_path):
    now = NOW_MS()
    monkeypatch.setattr(world_layers, '_cache', {'source': 'USGS', 'fetched_at': now, 'generated_at': now, 'events': [
        {'id': 'us7000abcd', 'lat': 34.1, 'lng': 35.6, 'depth_km': 10.0, 'magnitude': 4.2, 'place': '10 km W of Byblos',
         'time': now - 600000, 'updated': now - 300000, 'review_status': 'reviewed', 'url': 'https://x'}]})
    monkeypatch.setattr(world_layers, '_refresh_failed', False)
    from collections import OrderedDict
    monkeypatch.setattr(world_flights, '_cache', OrderedDict({(34.1, 35.6): {'failed': False, 'next': 0, 'snapshot': {
        'source': 'ADSB.lol', 'fetched_at': now, 'generated_at': now, 'aircraft': [
            {'id': 'abc123', 'callsign': 'MEA313', 'registration': 'OD-MRL', 'aircraft_type': 'A320', 'lat': 34.0,
             'lng': 35.5, 'position_at': now - 5000, 'on_ground': False, 'altitude_ft': 12000,
             'altitude_kind': 'geometric', 'speed_knots': 280, 'track_deg': 90, 'position_source': 'adsb_icao'}]}}}))
    snap = world_satellites._snapshot([station_row()], now)
    monkeypatch.setattr(world_satellites, '_read', lambda: {'snapshot': snap, 'next_attempt': 0, 'paused': False, 'failed': False})


def test_sgp4_geodetic_position_and_old_epoch_filter():
    now = time.time()
    records = live.propagate([station_row()], now)
    assert len(records) == 1
    r = records[0]
    assert r['kind'] == 'calculated' and 300000 < r['altitude_m'] < 500000 and abs(r['lat']) <= 52
    assert live.propagate([station_row()], now + 8 * 86400) == []


def test_scene_summarises_each_layer_from_its_own_cache(caches):
    scene = live.scene_context(layers=['earthquakes', 'flights', 'satellites'])
    by = {l['layer']: l for l in scene['layers']}
    assert by['earthquakes']['source'] == 'USGS' and by['earthquakes']['count'] == 1 and by['earthquakes']['status'] == 'fresh'
    assert by['flights']['source'] == 'ADSB.lol' and by['flights']['count'] == 1
    assert by['satellites']['source'] == 'CelesTrak' and by['satellites']['count'] == 1
    assert scene['selected'] is None


@pytest.mark.parametrize('identity,check', [
    ('flights:abc123', lambda c: c['entity']['label'] == 'MEA313' and c['entity']['altitude_ft'] == 12000 and c['position_status'] == 'current'),
    ('earthquakes:us7000abcd', lambda c: c['entity']['magnitude'] == 4.2 and 'Byblos' in c['entity']['label']),
    ('satellites:25544', lambda c: c['entity']['label'] == 'ISS TEST' and 300000 < c['entity']['altitude_m'] < 500000),
])
def test_selected_record_is_resolved_on_the_server(caches, identity, check):
    context = live.scene_context(identity, ['flights'])['selected']
    assert check(context), context


def test_unknown_or_malformed_selection_is_refused(caches):
    with pytest.raises(HTTPException) as gone:
        live.entity_context('flights:ffffff')
    assert gone.value.status_code == 409
    for bad in ('nonsense', 'opensky:abc', 'x' * 200, 5):
        with pytest.raises(HTTPException):
            live.entity_context(bad)
    with pytest.raises(HTTPException):
        live.scene_context(layers=['not-a-layer'])


def test_empty_caches_are_reported_unavailable(monkeypatch):
    monkeypatch.setattr(world_layers, '_cache', None)
    assert live.scene_context(layers=['earthquakes'])['layers'][0]['status'] == 'unavailable'


def test_chat_gets_server_selection_never_client_metadata(caches, monkeypatch):
    import config
    from agent import conversations
    calls, saved = [], []

    class Memory:
        messages = [{'role': 'user'}]

    class Agent:
        def _get_channel(self, *args):
            return Memory(), threading.Lock()

        def run(self, text, **kwargs):
            calls.append(text)
            return 'Aircraft explanation'
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'world-live-test')
    monkeypatch.setattr(server, '_agent_ref', Agent())
    monkeypatch.setattr(server, '_chat_lock', None)
    monkeypatch.setattr(server, '_throttle', AuthThrottle())
    monkeypatch.setattr(conversations, 'add_message', lambda t, role, text: saved.append((role, text)))
    with TestClient(server.app) as client:
        response = client.post('/api/chat', headers={'Authorization': 'Bearer world-live-test'}, json={
            'message': 'Explain the selection', 'thread_id': 77, 'world_entity_id': 'flights:abc123',
            'world_layer_ids': ['flights'], 'world_context': {'country': 'FAKE CLIENT DATA'}})
    assert response.status_code == 200 and response.json()['response'] == 'Aircraft explanation'
    assert 'ADSB.lol' in calls[0] and 'MEA313' in calls[0] and 'FAKE CLIENT DATA' not in calls[0]
    assert saved[0] == ('user', 'Explain the selection')
