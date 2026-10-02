"""Provider boundaries, timestamps, retry/caching and real SGP4 calculations."""
import asyncio
import json
import time
from datetime import datetime, timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from dashboard import world_live as live, server
from dashboard.ratelimit import AuthThrottle

@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    live._cache.clear();live._attempts.clear()
    monkeypatch.setattr(live,'_locks',{n:asyncio.Lock() for n in live.SOURCES})
    monkeypatch.setattr(live,'_flight_day',(0,0))
    monkeypatch.setattr(live,'_token',None)
    monkeypatch.delenv('OPENSKY_CLIENT_ID',raising=False)
    monkeypatch.delenv('OPENSKY_CLIENT_SECRET',raising=False)

def flight_payload():
    return {'states':[['abc123','APEX1','Lebanon',time.time()-3,time.time(),35.6,34.1,2000,False,100,90,0,None,2100]]}

def test_auth_and_unknown_layers(monkeypatch):
    import config
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','world-live-test')
    monkeypatch.setattr(server,'_throttle',AuthThrottle())
    with TestClient(server.app) as client:
        assert client.get('/api/world/layers/flights').status_code==401
        headers={'Authorization':'Bearer world-live-test'}
        assert client.get('/api/world/layers/unknown',headers=headers).status_code==404
        assert client.get('/api/world/layers/flights?lat=NaN',headers=headers).status_code in (400,422)
        assert client.post('/api/chat',headers=headers,json={'message':'Hi','world_entity_id':{'source':'fake'}}).status_code==400

def test_records_reject_invalid_numbers_and_keep_units():
    payload=flight_payload();bad=list(payload['states'][0]);bad[6]=True;payload['states'].append(bad)
    bad=list(payload['states'][0]);bad[5]=float('nan');payload['states'].append(bad)
    rows=live.flight_records(payload)
    assert len(rows)==1 and rows[0]['altitude_m']==2100 and rows[0]['kind']=='observed'
    assert live.flight_records({'states':None})==[]
    with pytest.raises(ValueError):live.flight_records({'states':'bad'})

def test_quake_event_time_and_depth_are_not_render_height():
    payload={'features':[{'id':'test','properties':{'time':1700000000000,'mag':3.2,'title':'<script>event</script>'},
                          'geometry':{'type':'Point','coordinates':[35,34,20]}}]}
    row=live.quake_records(payload)[0]
    assert row['observed_at']==1700000000 and row['altitude_m']==0 and row['depth_km']==20
    payload['features'][0]['properties']['mag']=float('inf')
    assert live.quake_records(payload)==[]

def test_cache_coalesces_and_failure_retains_stale_data(monkeypatch):
    calls=[];clock=[time.time()]
    monkeypatch.setattr(live.time,'time',lambda:clock[0])
    async def read(client,url,**kwargs):
        calls.append((url,kwargs))
        if len(calls)>1:raise httpx.ConnectError('offline')
        return flight_payload()
    monkeypatch.setattr(live,'read_json',read)
    async def scenario():
        first,second=await asyncio.gather(live.feed('flights',34,35),live.feed('flights',34,35))
        assert first['status']==second['status']=='fresh' and len(calls)==1
        assert calls[0][1]['params']=={'lamin':30,'lomin':35,'lamax':35,'lomax':40}
        assert 'Authorization' not in calls[0][1]['headers']
        clock[0]+=301
        stale=await live.feed('flights',34,35)
        assert stale['status']=='stale' and stale['records'][0]['id']=='flights:abc123'
        assert (await live.feed('flights',34,35))['status']=='stale' and len(calls)==2
        context=live.entity_context('flights:abc123')
        assert context['position_status']=='aged' and context['feed_status']=='stale'
    asyncio.run(scenario())

def test_daily_budget_and_antimeridian_bounds(monkeypatch):
    live._flight_day=(int(time.time()//86400),300)
    result=asyncio.run(live.feed('flights',90,180))
    assert result['status']=='unavailable' and result['records']==[]
    assert ('flights',85,175) in live._attempts

def test_oversized_response_stops_reading():
    class Response:
        def raise_for_status(self):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def aiter_bytes(self):yield b'x'*2_000_001
    class Client:
        def stream(self,*args,**kwargs):return Response()
    with pytest.raises(ValueError):asyncio.run(live.read_json(Client(),'https://example.test'))

def test_provider_retry_window_is_honored(monkeypatch):
    calls=[]
    async def limited(client,url,**kwargs):
        calls.append(url)
        response=httpx.Response(429,headers={'X-Rate-Limit-Retry-After-Seconds':'1800'},request=httpx.Request('GET',url))
        raise httpx.HTTPStatusError('limited',request=response.request,response=response)
    monkeypatch.setattr(live,'read_json',limited)
    async def scenario():
        result=await live.feed('flights',34,35)
        assert result['status']=='unavailable' and result['retry_after']>=1799
        await live.feed('flights',34,35)
        assert len(calls)==1
    asyncio.run(scenario())

def test_oauth_exchange_is_cached_and_only_provider_credentials_are_sent(monkeypatch):
    monkeypatch.setenv('OPENSKY_CLIENT_ID','test-id')
    monkeypatch.setenv('OPENSKY_CLIENT_SECRET','test-secret')
    calls=[]
    class Client:
        async def post(self,url,**kwargs):
            calls.append((url,kwargs))
            return httpx.Response(200,json={'access_token':'provider-token','expires_in':1800},request=httpx.Request('POST',url))
    async def scenario():
        first=await live.flight_headers(Client());second=await live.flight_headers(Client())
        assert first==second=={'Authorization':'Bearer provider-token'} and len(calls)==1
        assert calls[0][1]['data']=={'grant_type':'client_credentials','client_id':'test-id','client_secret':'test-secret'}
    asyncio.run(scenario())

def test_sgp4_geodetic_position_and_old_epoch_filter():
    now=time.time();epoch=datetime.fromtimestamp(now,timezone.utc).replace(tzinfo=None).isoformat()
    row={'OBJECT_NAME':'ISS TEST','NORAD_CAT_ID':25544,'EPOCH':epoch,'MEAN_MOTION':15.5,'ECCENTRICITY':0.0005,
         'INCLINATION':51.6,'RA_OF_ASC_NODE':20,'ARG_OF_PERICENTER':30,'MEAN_ANOMALY':40,'EPHEMERIS_TYPE':0,
         'CLASSIFICATION_TYPE':'U','ELEMENT_SET_NO':999,'REV_AT_EPOCH':1,'BSTAR':0.00001,
         'MEAN_MOTION_DOT':0.0001,'MEAN_MOTION_DDOT':0,'OBJECT_ID':'1998-067A'}
    records=live.propagate([row],now)
    assert len(records)==1
    r=records[0];assert r['kind']=='calculated' and 300000<r['altitude_m']<500000 and abs(r['lat'])<=52
    assert r['epoch']==pytest.approx(now,abs=.001)
    assert live.propagate([row],now+8*86400)==[]

def test_unknown_context_never_uses_client_metadata():
    with pytest.raises(Exception) as error:live.entity_context('flights:unknown')
    assert error.value.status_code==409

def test_chat_receives_server_selection_and_preserves_user_message(monkeypatch):
    import threading
    import config
    from agent import conversations
    calls=[];saved=[]
    key=('flights',30,35)
    live._cache[key]={'fetched_at':time.time(),'data':live.flight_records(flight_payload())}
    class Memory: messages=[{'role':'user'}]
    class Agent:
        def _get_channel(self,*args):return Memory(),threading.Lock()
        def run(self,text,**kwargs):calls.append(text);return 'Aircraft explanation'
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','world-live-test')
    monkeypatch.setattr(server,'_agent_ref',Agent())
    monkeypatch.setattr(server,'_chat_lock',None)
    monkeypatch.setattr(server,'_throttle',AuthThrottle())
    monkeypatch.setattr(conversations,'add_message',lambda t,role,text:saved.append((role,text)))
    with TestClient(server.app) as client:
        response=client.post('/api/chat',headers={'Authorization':'Bearer world-live-test'},json={
            'message':'Explain the selection','thread_id':77,'world_entity_id':'flights:abc123',
            'world_layer_ids':['flights'],'world_context':{'country':'FAKE CLIENT DATA'}})
    assert response.status_code==200 and response.json()['response']=='Aircraft explanation'
    assert 'OpenSky Network' in calls[0] and 'abc123' in calls[0] and 'FAKE CLIENT DATA' not in calls[0]
    assert saved[0]==('user','Explain the selection')
    assert live.scene_context(layers=['earthquakes'])['layers'][0]['status']=='unavailable'
    with pytest.raises(Exception):live.scene_context(layers=['not-a-layer'])
