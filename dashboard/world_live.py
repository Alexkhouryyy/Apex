"""What Celine is told about World View: the layers you have on and the record
you selected, resolved from the server's own feed caches (world_layers.py,
world_flights.py, world_satellites.py), never from what the page claims.

The feeds themselves live in those three modules. This file used to carry a
second, parallel set of feeds; they were merged into the first so each layer
exists once.
"""
import math
import time
from datetime import datetime, timezone

from fastapi import HTTPException

LAYERS = ('earthquakes', 'flights', 'satellites')


def numeric(v, low=-1e15, high=1e15):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and low <= v <= high


def label(v):
    return str(v or '').strip()[:160]


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
            # The layer cache keeps only the orbit itself; sgp4 also reads three
            # bookkeeping fields that do not change the calculated position.
            fields = {'ELEMENT_SET_NO': 999, 'REV_AT_EPOCH': 0, 'OBJECT_ID': '', **row,
                      'CENTER_NAME':'EARTH','REF_FRAME':'TEME','TIME_SYSTEM':'UTC','MEAN_ELEMENT_THEORY':'SGP4'}
            fields['EPOCH'] = str(fields['EPOCH']).rstrip('Z')     # sgp4 wants the bare UTC time
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



def _now_ms():
    return int(time.time() * 1000)


def _view(layer):
    """(snapshot, status) for a layer from its own cache, or (None, 'unavailable')."""
    if layer == 'earthquakes':
        from dashboard import world_layers as w
        snap = w._cache
        if not snap:
            return None, 'unavailable'
        stale = w._refresh_failed or _now_ms() - snap['fetched_at'] > w._STALE_MS
        return snap, 'stale' if stale else 'fresh'
    if layer == 'flights':
        from dashboard import world_flights as w
        entries = [e for e in w._cache.values() if e.get('snapshot')]
        if not entries:
            return None, 'unavailable'
        entry = max(entries, key=lambda e: e['snapshot']['fetched_at'])
        snap = entry['snapshot']
        stale = entry['failed'] or _now_ms() - snap['fetched_at'] > 90000
        return snap, 'stale' if stale else 'fresh'
    from dashboard import world_satellites as w
    try:
        data = w._read()
    except (ValueError, OSError):
        return None, 'unavailable'
    snap = data.get('snapshot')
    if not snap:
        return None, 'unavailable'
    stale = data['failed'] or _now_ms() - snap['fetched_at'] > 21600000
    return snap, 'stale' if stale else 'fresh'


_ROWS = {'earthquakes': 'events', 'flights': 'aircraft', 'satellites': 'satellites'}
_COVERAGE = {'earthquakes': 'M2.5+ events in the past 24 hours',
             'flights': '250 nm around the loaded area; receiver coverage varies',
             'satellites': 'Space stations group; positions calculated (SGP4), not observed telemetry'}


def _entity(layer, record, snap):
    now = time.time()
    if layer == 'earthquakes':
        return {'id': 'earthquakes:' + record['id'], 'label': record['place'], 'lat': record['lat'], 'lng': record['lng'],
                'magnitude': record['magnitude'], 'depth_km': record['depth_km'],
                'observed_at': record['time'] / 1000, 'review_status': record['review_status'], 'kind': 'observed'}, \
            max(0, int(now - record['time'] / 1000)), 'current'
    if layer == 'flights':
        age = max(0, int(now - record['position_at'] / 1000))
        return {'id': 'flights:' + record['id'], 'label': record['callsign'] or record['registration'] or record['id'],
                'lat': record['lat'], 'lng': record['lng'], 'altitude_ft': record['altitude_ft'],
                'speed_knots': record['speed_knots'], 'track_deg': record['track_deg'], 'on_ground': record['on_ground'],
                'aircraft_type': record['aircraft_type'], 'observed_at': record['position_at'] / 1000, 'kind': 'observed'}, \
            age, 'aged' if age > 120 else 'current'
    calculated = propagate([record['omm']], now)
    epoch = record['epoch_at'] / 1000
    entity = calculated[0] if calculated else {'id': 'satellites:' + record['id'], 'label': record['name'],
                                               'kind': 'calculated', 'note': 'position could not be calculated'}
    entity['label'] = record['name']
    return entity, max(0, int(now - epoch)), 'aged' if now - epoch > 3 * 86400 else 'current'


def entity_context(identity):
    if not isinstance(identity, str) or len(identity) > 180 or ':' not in identity:
        raise HTTPException(400, 'Invalid world selection')
    layer, _, wanted = identity.partition(':')
    if layer not in LAYERS:
        raise HTTPException(400, 'Invalid world selection')
    snap, status = _view(layer)
    for record in (snap or {}).get(_ROWS[layer], []):
        if record['id'] == wanted:
            entity, age, position = _entity(layer, record, snap)
            return {'source': snap['source'], 'feed_status': status, 'fetched_at': snap['fetched_at'] / 1000,
                    'coverage': _COVERAGE[layer], 'entity': entity,
                    'position_age_seconds': age, 'position_status': position}
    raise HTTPException(409, 'Selection expired. Refresh its layer and select it again.')


def scene_context(identity=None, layers=None):
    if layers is None:
        layers = []
    if not isinstance(layers, list) or len(layers) > 3 or any(not isinstance(n, str) or n not in LAYERS for n in layers):
        raise HTTPException(400, 'Invalid world layers')
    summary = []
    for layer in dict.fromkeys(layers):
        snap, status = _view(layer)
        if snap is None:
            summary.append({'layer': layer, 'status': 'unavailable', 'count': 0})
            continue
        summary.append({'layer': layer, 'status': status, 'source': snap['source'], 'fetched_at': snap['fetched_at'] / 1000,
                        'coverage': _COVERAGE[layer], 'count': len(snap[_ROWS[layer]])})
    return {'layers': summary, 'selected': entity_context(identity) if identity is not None else None}
