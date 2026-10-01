"""Regional ADSB.lol positions, shared bounded snapshots, no flight history."""
import asyncio
import json
import math
import re
from collections import OrderedDict
from time import monotonic, time as wall_time

import httpx
from fastapi import APIRouter, HTTPException, Query
from dashboard.world_layers import _number

router = APIRouter(prefix='/api/world/layers')
_BASE = 'https://api.adsb.lol/v2/lat/{lat}/lon/{lng}/dist/250'
_AGENT = 'Apex-WorldView/1.0 (+https://github.com/Alexkhouryyy/Apex/issues)'
_MAX_BYTES = 2 * 1024 * 1024
_cache = OrderedDict()
_lock = asyncio.Lock()
_next_request = 0.0


def _text(value, limit=40):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _optional(value, low, high):
    return value if _number(value, low, high) else None


def _snapshot(payload, fetched_at, area):
    if not isinstance(payload, dict) or payload.get('msg') != 'No error' or not isinstance(payload.get('ac'), list):
        raise ValueError('Invalid aircraft feed')
    generated = payload.get('now')
    if not _number(generated, 946684800000, fetched_at + 30000):
        raise ValueError('Invalid aircraft timestamp')
    records = {}
    for raw in payload['ac'][:3000]:
        if not isinstance(raw, dict):
            continue
        identity = raw.get('hex')
        lat, lng, age = raw.get('lat'), raw.get('lon'), raw.get('seen_pos')
        if (not isinstance(identity, str) or not re.fullmatch(r'[0-9a-fA-F]{6}', identity)
                or raw.get('type') not in ('adsb_icao', 'adsb_icao_nt', 'mlat', 'mode_s', 'other')
                or not _number(lat, -90, 90) or not _number(lng, -180, 180)
                or not _number(age, 0, 120)):
            continue
        identity = identity.lower()
        lat_delta, lng_delta = math.radians(lat - area[0]), math.radians(lng - area[1])
        h = (math.sin(lat_delta / 2) ** 2 + math.cos(math.radians(lat))
             * math.cos(math.radians(area[0])) * math.sin(lng_delta / 2) ** 2)
        if 6371000 * 2 * math.asin(min(1, math.sqrt(h))) > 250 * 1852 + 1000:
            continue
        ground = raw.get('alt_baro') == 'ground'
        geom = _optional(raw.get('alt_geom'), -2000, 100000)
        baro = _optional(raw.get('alt_baro'), -2000, 100000)
        altitude = geom if geom is not None else baro
        record = {'id': identity, 'callsign': _text(raw.get('flight'), 16),
                  'registration': _text(raw.get('r'), 20), 'aircraft_type': _text(raw.get('t'), 12),
                  'lat': lat, 'lng': lng, 'position_at': int(generated - age * 1000),
                  'on_ground': ground, 'altitude_ft': altitude,
                  'altitude_kind': 'geometric' if geom is not None else 'barometric' if baro is not None else 'unknown',
                  'speed_knots': _optional(raw.get('gs'), 0, 2000),
                  'track_deg': _optional(raw.get('track'), 0, 360),
                  'position_source': raw.get('type')}
        if identity not in records or record['position_at'] > records[identity]['position_at']:
            records[identity] = record
    ordered = sorted(records.values(), key=lambda r: (-r['position_at'], r['id']))
    return {'source': 'ADSB.lol', 'source_url': 'https://www.adsb.lol/',
            'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
            'area': {'lat': area[0], 'lng': area[1], 'radius_nm': 250},
            'generated_at': int(generated), 'fetched_at': fetched_at,
            'refresh_after_seconds': 30, 'truncated': len(ordered) > 500 or len(payload['ac']) > 3000,
            'aircraft': ordered[:500]}


def _response(entry):
    snapshot = entry['snapshot']
    if snapshot is None:
        raise HTTPException(503, 'Aircraft feed unavailable. Try again shortly.',
                            headers={'Retry-After': str(max(1, math.ceil(entry['next'] - monotonic())))})
    now = int(wall_time() * 1000)
    return {**snapshot, 'refresh_failed': entry['failed'], 'stale': entry['failed']
            or now - snapshot['generated_at'] > 90000 or now - snapshot['fetched_at'] > 90000}


@router.get('/flights')
async def flights(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    global _next_request
    # Stable region keys bound cache growth; normalized center is returned to UI.
    area = (round(lat, 1), round(lng, 1))
    async with _lock:
        now = monotonic()
        entry = _cache.get(area)
        if entry and now < entry['next']:
            _cache.move_to_end(area)
            return _response(entry)
        if now < _next_request:
            if entry and entry['snapshot']:
                return _response(entry)
            raise HTTPException(503, 'Aircraft provider is cooling down. Try again shortly.',
                                headers={'Retry-After': str(max(1, math.ceil(_next_request - now)))})
        if entry is None:
            entry = {'snapshot': None, 'next': 0, 'failed': False}
            _cache[area] = entry
        retry_seconds = 30
        rate_limited = False
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                async with client.stream('GET', _BASE.format(lat=area[0], lng=area[1]),
                                         headers={'User-Agent': _AGENT}) as response:
                    if response.status_code == 429:
                        rate_limited = True
                        try:
                            retry_seconds = max(30, min(86400, int(response.headers.get('Retry-After', '30'))))
                        except ValueError:
                            pass
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > _MAX_BYTES:
                            raise ValueError('Aircraft feed too large')
                        content.extend(chunk)
            entry['snapshot'] = _snapshot(json.loads(content), int(wall_time() * 1000), area)
            entry['failed'] = False
        except (httpx.HTTPError, ValueError, UnicodeError, OverflowError):
            entry['failed'] = True
        finally:
            entry['next'] = monotonic() + retry_seconds
            _next_request = monotonic() + (retry_seconds if rate_limited else 5)
            _cache.move_to_end(area)
            while len(_cache) > 16:
                _cache.popitem(last=False)
        return _response(entry)
