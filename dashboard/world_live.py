"""Bounded public feeds. Fixed destinations; no dashboard credentials leave Apex."""
import asyncio
import copy
import json
import math
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
SOURCES = {
    'flights': ('OpenSky Network', 'https://opensky-network.org/api/states/all', 60),
    'earthquakes': ('USGS', 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson', 60),
    'satellites': ('CelesTrak', 'https://celestrak.org/NORAD/elements/gp.php', 7200),
}
_cache = OrderedDict()
_locks = {name: asyncio.Lock() for name in SOURCES}
_attempts = {}
_token = None
_token_until = 0
_flight_day = (0, 0)

def numeric(v, low=-1e15, high=1e15):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and low <= v <= high

def label(v):
    return str(v or '').strip()[:160]

def flight_records(data):
    if not isinstance(data, dict) or not isinstance(data.get('states'), (list, type(None))): raise ValueError('Invalid flight response')
    result = []
    for row in (data['states'] or [])[:12000]:
        if not isinstance(row, list) or len(row) < 14: continue
        if not isinstance(row[0], str) or not re.fullmatch('[a-fA-F0-9]{6}', row[0]): continue
        if not numeric(row[5], -180, 180) or not numeric(row[6], -90, 90) or not numeric(row[3], 0): continue
        altitude = row[13] if numeric(row[13], -500, 30000) else row[7]
        result.append({'id':'flights:'+row[0].lower(), 'label':label(row[1]) or row[0], 'lat':row[6], 'lng':row[5],
                       'altitude_m':altitude if numeric(altitude, -500, 30000) else 0, 'observed_at':row[3],
                       'kind':'observed', 'country':label(row[2]), 'on_ground':row[8] is True,
                       'speed_m_s':row[9] if numeric(row[9], 0, 2000) else None,
                       'heading_deg':row[10] if numeric(row[10], 0, 360) else None})
    return result[:1500]

def quake_records(data):
    if not isinstance(data, dict) or not isinstance(data.get('features'), list): raise ValueError('Invalid earthquake response')
    result = []
    for feature in data['features'][:3000]:
        if not isinstance(feature, dict): continue
        props, geometry = feature.get('properties'), feature.get('geometry')
        if not isinstance(props, dict) or not isinstance(geometry, dict) or geometry.get('type') != 'Point': continue
        p = geometry.get('coordinates')
        if not isinstance(p, list) or len(p) < 3 or not numeric(p[0], -180, 180) or not numeric(p[1], -90, 90): continue
        if not numeric(props.get('time'), 0) or not numeric(props.get('mag'), -3, 12): continue
        identity = label(feature.get('id'))
        if not identity: continue
        result.append({'id':'earthquakes:'+identity, 'label':label(props.get('title') or props.get('place')),
                       'lat':p[1], 'lng':p[0], 'altitude_m':0, 'observed_at':props['time']/1000,
                       'kind':'observed', 'magnitude':props['mag'], 'depth_km':p[2] if numeric(p[2], -10, 1000) else None})
    return result

def propagate(rows, now):
    from sgp4.api import Satrec, jday
    from sgp4 import omm
    result = []
    date = datetime.fromtimestamp(now, timezone.utc)
    jd, fr = jday(date.year,date.month,date.day,date.hour,date.minute,date.second+date.microsecond/1e6)
    theta = math.radians((280.46061837 + 360.98564736629*((jd+fr)-2451545.0)) % 360)
    for row in rows[:150]:
        if not isinstance(row, dict): continue
        try:
            fields = {**row, 'CENTER_NAME':'EARTH','REF_FRAME':'TEME','TIME_SYSTEM':'UTC','MEAN_ELEMENT_THEORY':'SGP4'}
            epoch = datetime.fromisoformat(str(row['EPOCH']).replace('Z','+00:00'))
            if epoch.tzinfo is None: epoch = epoch.replace(tzinfo=timezone.utc)
            epoch_time = epoch.timestamp()
            if abs(now-epoch_time) > 7*86400: continue
            sat = Satrec(); omm.initialize(sat, fields)
            error, pos, _ = sat.sgp4(jd, fr)
            if error or not all(math.isfinite(v) for v in pos): continue
            x = pos[0]*math.cos(theta)+pos[1]*math.sin(theta)
            y = -pos[0]*math.sin(theta)+pos[1]*math.cos(theta); z = pos[2]
            radius = math.hypot(x,y); latitude = math.atan2(z, radius)
            for _ in range(8):
                normal = 6378.137/math.sqrt(1-0.00669437999014*math.sin(latitude)**2)
                latitude = math.atan2(z+0.00669437999014*normal*math.sin(latitude),radius)
            height = radius/max(math.cos(latitude),1e-12)-normal
            identity = str(int(row['NORAD_CAT_ID']))
            if not 0 < int(identity) < 1000000 or not numeric(height, 0, 100000): continue
            result.append({'id':'satellites:'+identity,'label':label(row.get('OBJECT_NAME')) or identity,
                           'lat':math.degrees(latitude),'lng':math.degrees(math.atan2(y,x)), 'altitude_m':height*1000,
                           'kind':'calculated','calculated_at':now,'epoch':epoch_time,'observed_at':epoch_time})
        except (ValueError, TypeError, KeyError, OverflowError): continue
    return result

async def read_json(client, url, **kwargs):
    async with client.stream('GET', url, **kwargs) as response:
        response.raise_for_status()
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 2_000_000: raise ValueError('Feed too large')
    return json.loads(raw)

async def flight_headers(client):
    global _token, _token_until
    identity, secret = os.getenv('OPENSKY_CLIENT_ID',''), os.getenv('OPENSKY_CLIENT_SECRET','')
    if not identity or not secret: return {}
    if not _token or time.time() >= _token_until:
        response = await client.post('https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token',
                                     data={'grant_type':'client_credentials','client_id':identity,'client_secret':secret})
        response.raise_for_status()
        if len(response.content) > 16000: raise ValueError('Invalid token response')
        data = response.json(); token = data.get('access_token')
        if not isinstance(token, str) or len(token)>12000: raise ValueError('Invalid access token')
        _token = token; _token_until = time.time()+max(1,min(int(data.get('expires_in',1800))-30,1800))
    return {'Authorization':'Bearer '+_token}

def snapshot(layer, key, error=None):
    source, url, ttl = SOURCES[layer]
    cached = _cache.get(key); now = time.time()
    last, _, delay = _attempts.get(key,(0,None,60))
    retry = max(1,round(last+delay-now)) if error else ttl
    if not cached:
        return {'layer':layer,'status':'unavailable','source':source,'source_url':url,'records':[],
                'fetched_at':None,'age_seconds':None,'retry_after':retry,'message':error or 'No data received'}
    age = max(0,now-cached['fetched_at'])
    records = propagate(cached['data'], now) if layer == 'satellites' else copy.deepcopy(cached['data'])
    return {'layer':layer,'status':'stale' if error or age>ttl else 'fresh', 'source':source,'source_url':url,
            'records':records, 'fetched_at':cached['fetched_at'],'age_seconds':round(age), 'retry_after':retry,
            'message':error or '', 'coverage':'5° region around view; receiver coverage varies' if layer=='flights' else
            'M2.5+ events in past 24 hours' if layer=='earthquakes' else 'Space stations group; SGP4 calculation, not observed telemetry'}

async def feed(layer, lat=25.0, lng=35.0):
    global _flight_day, _token
    if layer not in SOURCES: raise HTTPException(404,'Unknown world layer')
    if not numeric(lat,-90,90) or not numeric(lng,-180,180): raise HTTPException(400,'Invalid region')
    south = max(-90,min(85,math.floor(lat/5)*5)); west = max(-180,min(175,math.floor(lng/5)*5))
    key = (layer,south,west) if layer=='flights' else (layer,)
    async with _locks[layer]:
        now = time.time(); ttl = SOURCES[layer][2]; cached = _cache.get(key)
        if cached and now-cached['fetched_at'] < ttl: return snapshot(layer,key)
        last, error, delay = _attempts.get(key,(0,None,60))
        if now-last < delay: return snapshot(layer,key,error)
        try:
            if layer=='flights':
                day, count = _flight_day
                if day != int(now//86400): day,count=int(now//86400),0
                budget=3000 if os.getenv('OPENSKY_CLIENT_ID') and os.getenv('OPENSKY_CLIENT_SECRET') else 300
                if count>=budget: raise ValueError('Local daily flight request budget reached')
                _flight_day=(day,count+1)
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
                params = {}; headers = {'User-Agent':'Apex-WorldView/2.0'}
                if layer=='flights':
                    params={'lamin':south,'lomin':west,'lamax':south+5,'lomax':west+5}
                    headers.update(await flight_headers(client))
                elif layer=='satellites': params={'GROUP':'stations','FORMAT':'json'}
                data = await read_json(client,SOURCES[layer][1],params=params,headers=headers)
                if layer=='flights': records=flight_records(data)
                elif layer=='earthquakes': records=quake_records(data)
                else:
                    if not isinstance(data,list) or len(data)>5000: raise ValueError('Invalid station response')
                    data=data[:150]
                    propagate(data,now)  # Verify dependency before caching.
                    records=data
            _cache[key]={'fetched_at':now,'data':records}
            _cache.move_to_end(key)
            while len(_cache)>16:
                evicted,_ = _cache.popitem(last=False); _attempts.pop(evicted,None)
            _attempts[key]=(now,None,ttl)
            return snapshot(layer,key)
        except asyncio.CancelledError: raise
        except (httpx.HTTPError,ValueError,TypeError,ImportError) as failure:
            delay=60
            error = 'Provider unavailable; retry later.'
            if isinstance(failure,ImportError):error='Satellite propagation dependency missing. Install sgp4>=2.24,<3.'
            elif isinstance(failure,ValueError) and 'budget' in str(failure):
                delay=max(60,int((int(now//86400)+1)*86400-now));error='Local daily flight budget reached. Configure OpenSky OAuth for longer sessions.'
            elif isinstance(failure,httpx.HTTPStatusError):
                code=failure.response.status_code
                error=f'Provider returned HTTP {code}; check availability or account access.'
                if code==401 and layer=='flights':_token=None
                if code==429:
                    value=failure.response.headers.get('X-Rate-Limit-Retry-After-Seconds',failure.response.headers.get('Retry-After','60'))
                    try:delay=max(60,min(86400,int(value)))
                    except ValueError:delay=300
                    error='Provider rate limited this layer; respecting its retry window.'
            _attempts[key]=(now,error,delay)
            if len(_attempts)>64: _attempts.pop(next(iter(_attempts)))
            return snapshot(layer,key,error)

@router.get('/api/world/layers/{layer}')
async def layer_feed(layer:str,lat:float=Query(25,ge=-90,le=90),lng:float=Query(35,ge=-180,le=180)):
    return await feed(layer,lat,lng)

def entity_context(identity):
    """Resolve server-known records, never arbitrary client-supplied metadata."""
    if not isinstance(identity,str) or len(identity)>180: raise HTTPException(400,'Invalid world selection')
    for key in reversed(_cache):
        view = snapshot(key[0],key)
        for record in view['records']:
            if record['id']==identity:
                return {'source':view['source'],'feed_status':view['status'],'fetched_at':view['fetched_at'],
                        'coverage':view['coverage'],'entity':record,
                        'position_age_seconds':max(0,int(time.time()-record['observed_at'])),
                        'position_status':'aged' if (record['kind']=='calculated' and time.time()-record['epoch']>3*86400)
                        or (key[0]=='flights' and time.time()-record['observed_at']>120) else 'current'}
    raise HTTPException(409,'Selection expired. Refresh its layer and select it again.')

def scene_context(identity=None, layers=None):
    if layers is None: layers=[]
    if not isinstance(layers,list) or len(layers)>3 or any(not isinstance(n,str) or n not in SOURCES for n in layers):
        raise HTTPException(400,'Invalid world layers')
    summary=[]
    for layer in dict.fromkeys(layers):
        key=next((k for k in reversed(_cache) if k[0]==layer),None)
        if key is None: summary.append({'layer':layer,'status':'unavailable','count':0});continue
        view=snapshot(layer,key)
        summary.append({k:view[k] for k in ('layer','status','source','fetched_at','coverage')})
        summary[-1]['count']=len(view['records'])
    return {'layers':summary,'selected':entity_context(identity) if identity is not None else None}
