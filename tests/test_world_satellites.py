"""Validated OMMs, persistent request spacing and manual outage recovery."""
import asyncio
import copy
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from dashboard import server, world_satellites as satellites
from dashboard.ratelimit import AuthThrottle

NOW=1790875200000
OMM=json.loads((Path(__file__).parent/'fixtures/world-station-omm.json').read_text())

@pytest.fixture
def rig(monkeypatch,tmp_path):
    import config
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','sat-test')
    monkeypatch.setattr(server,'_throttle',AuthThrottle())
    monkeypatch.setattr(satellites,'_lock',asyncio.Lock())
    path=tmp_path/'stations.json'
    monkeypatch.setattr(satellites,'_cache_file',lambda:path)
    clock={'ms':NOW};monkeypatch.setattr(satellites,'wall_time',lambda:clock['ms']/1000)
    calls,payload=[],{'status':200,'data':[OMM]}
    def handle(request):
        calls.append(request);return httpx.Response(payload['status'],json=payload['data'])
    original=httpx.AsyncClient
    def client(**kw):
        assert kw=={'timeout':10,'follow_redirects':False}
        return original(transport=httpx.MockTransport(handle),**kw)
    monkeypatch.setattr(satellites.httpx,'AsyncClient',client)
    with TestClient(server.app) as client:
        yield client,{'Authorization':'Bearer sat-test'},clock,calls,payload,path

def test_auth_valid_elements_and_persistent_cooldown(rig):
    client,headers,clock,calls,_,path=rig
    assert client.get('/api/world/layers/satellites').status_code==401
    data=client.get('/api/world/layers/satellites',headers=headers).json()
    assert data['source']=='CelesTrak' and data['format']=='OMM JSON'
    assert data['fetched_at']==NOW and data['next_attempt_at']==NOW+7200000
    r=data['satellites'][0];assert r['id']=='25544' and r['omm']['EPOCH'].endswith('Z')
    assert r['omm']['MEAN_MOTION']==OMM['MEAN_MOTION'] and not data['stale']
    assert 'authorization' not in calls[0].headers and str(calls[0].url)==satellites._URL
    assert path.exists()
    # Every API call reloads disk, equivalent to a fresh process with the saved file.
    clock['ms']+=1000
    assert client.get('/api/world/layers/satellites',headers=headers).json()['fetched_at']==NOW
    assert client.post('/api/world/layers/satellites/retry',headers=headers).status_code==200
    assert len(calls)==1

def test_http_error_stops_automatic_queries_across_restarts(rig):
    client,headers,clock,calls,payload,path=rig
    original=client.get('/api/world/layers/satellites',headers=headers).json()
    clock['ms']+=7200001;payload['status']=403
    data=client.get('/api/world/layers/satellites',headers=headers).json()
    assert data['stale'] and data['source_paused'] and data['refresh_failed']
    assert data['fetched_at']==original['fetched_at'] and data['satellites']==original['satellites']
    clock['ms']+=86400000
    for _ in range(3):client.get('/api/world/layers/satellites',headers=headers)
    assert len(calls)==2 and json.loads(path.read_text())['paused']
    payload['status']=200
    data=client.post('/api/world/layers/satellites/retry',headers=headers).json()
    assert not data['source_paused'] and not data['stale'] and len(calls)==3

def test_first_failure_negative_cache_and_auth_for_retry(rig):
    client,headers,clock,calls,payload,_=rig
    payload['status']=503
    assert client.post('/api/world/layers/satellites/retry').status_code==401
    for _ in range(3):assert client.get('/api/world/layers/satellites',headers=headers).status_code==503
    assert len(calls)==1
    assert client.post('/api/world/layers/satellites/retry',headers=headers).status_code==503
    assert len(calls)==1,'manual recovery must respect the original two-hour cooldown'
    clock['ms']+=7200001;payload['status']=200
    assert client.post('/api/world/layers/satellites/retry',headers=headers).status_code==200

def test_invalid_elements_duplicate_epoch_and_large_catalog_ids():
    valid={**OMM,'NORAD_CAT_ID':100123,'OBJECT_NAME':'<img src=x onerror=alert(1)>'}
    bad=[]
    for key,value in [('NORAD_CAT_ID',True),('EPOCH','bad'),('EPOCH','2026-99-99T00:00:00'),
                      ('MEAN_MOTION',float('nan')),('ECCENTRICITY',1),('INCLINATION',181),
                      ('REF_FRAME','ITRF'),('TIME_SYSTEM','TAI'),('BSTAR',True)]:
        bad.append({**OMM,key:value})
    data=satellites._snapshot([valid,OMM,{**OMM,'EPOCH':'2026-10-01T03:39:00.159Z'},None,*bad],NOW)
    assert len(data['satellites'])==2 and data['satellites'][0]['id']=='25544'
    assert data['satellites'][0]['omm']['EPOCH']=='2026-10-01T03:39:00.159Z'
    assert data['satellites'][1]['id']=='100123' and data['satellites'][1]['name'].startswith('<img')
    assert satellites._snapshot([],NOW)['satellites']==[]
    for value in ({},[None],[{**OMM,'EPOCH':'2027-01-01T00:00:00Z'}]):
        with pytest.raises(ValueError):satellites._snapshot(value,NOW)

def test_feed_bounds_and_storage_failure_never_hammer(rig,monkeypatch):
    client,headers,_,calls,_,path=rig
    monkeypatch.setattr(satellites,'_MAX_BYTES',20)
    assert client.get('/api/world/layers/satellites',headers=headers).status_code==503
    assert len(calls)==1
    assert json.loads(path.read_text())['paused']
    monkeypatch.setattr(satellites,'_MAX_BYTES',512*1024)
    path.write_text('broken')
    assert client.get('/api/world/layers/satellites',headers=headers).status_code==503
    assert len(calls)==1
    path.unlink()
    def fail(_):raise OSError('disk full')
    monkeypatch.setattr(satellites,'_write',fail)
    assert client.get('/api/world/layers/satellites',headers=headers).status_code==503
    assert len(calls)==1

def test_record_cap_and_source_snapshot_age(rig):
    rows=[{**OMM,'NORAD_CAT_ID':100000+i} for i in range(70)]
    data=satellites._snapshot(rows,NOW)
    assert len(data['satellites'])==64 and data['truncated']
    client,headers,clock,_,payload,_=rig
    client.get('/api/world/layers/satellites',headers=headers)
    clock['ms']+=21600001;payload['status']=500
    assert client.get('/api/world/layers/satellites',headers=headers).json()['stale']
