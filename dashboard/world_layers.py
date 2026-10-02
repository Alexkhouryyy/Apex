"""Bounded public-source data for World View; dashboard auth stays upstream."""
import asyncio
import json
import math
import re
from time import monotonic, time as wall_time

import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix='/api/world/layers')
_USGS_URL = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson'
_MAX_BYTES = 2 * 1024 * 1024
_MAX_EVENTS = 300
_REFRESH_SECONDS = 60
_STALE_MS = 5 * 60 * 1000
_cache = None
_next_attempt = 0.0
_refresh_failed = False
_lock = asyncio.Lock()


def _number(value, low, high):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and low <= value <= high)


def _snapshot(payload, fetched_at):
    if not isinstance(payload, dict) or payload.get('type') != 'FeatureCollection':
        raise ValueError('Invalid collection')
    metadata, features = payload.get('metadata'), payload.get('features')
    if not isinstance(metadata, dict) or not isinstance(features, list):
        raise ValueError('Invalid collection')
    generated = metadata.get('generated')
    if metadata.get('status') != 200 or not _number(generated, 946684800000, fetched_at + _STALE_MS):
        raise ValueError('Invalid feed timestamp')
    events = {}
    for feature in features[:1500]:
        if not isinstance(feature, dict):
            continue
        identity, props, geometry = feature.get('id'), feature.get('properties'), feature.get('geometry')
        if (not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', identity)
                or not isinstance(props, dict) or not isinstance(geometry, dict)):
            continue
        coords = geometry.get('coordinates')
        if (geometry.get('type') != 'Point' or not isinstance(coords, list) or len(coords) < 3
                or props.get('type') != 'earthquake'):
            continue
        lng, lat, depth = coords[:3]
        magnitude, occurred, updated = props.get('mag'), props.get('time'), props.get('updated')
        if (not _number(lng, -180, 180) or not _number(lat, -90, 90)
                or not _number(depth, -10, 1000) or not _number(magnitude, 2.5, 10)
                or not _number(occurred, generated - 86400000, generated + _STALE_MS)
                or not _number(updated, occurred, fetched_at + _STALE_MS)):
            continue
        place = props.get('place')
        if not isinstance(place, str) or not place.strip():
            place = 'Unnamed location'
        event = {'id': identity, 'lat': lat, 'lng': lng, 'depth_km': depth,
                 'magnitude': magnitude, 'place': place.strip()[:240],
                 'time': int(occurred), 'updated': int(updated),
                 'review_status': props.get('status') if props.get('status') in ('automatic', 'reviewed') else 'unknown',
                 # Rebuild the link from the validated ID; never trust provider URLs.
                 'url': 'https://earthquake.usgs.gov/earthquakes/eventpage/' + identity}
        if identity not in events or updated > events[identity]['updated']:
            events[identity] = event
    ordered = sorted(events.values(), key=lambda event: event['time'], reverse=True)
    return {'source': 'USGS', 'source_url': _USGS_URL, 'minimum_magnitude': 2.5,
            'window_hours': 24, 'generated_at': int(generated), 'fetched_at': fetched_at,
            'refresh_after_seconds': _REFRESH_SECONDS,
            'truncated': len(ordered) > _MAX_EVENTS or len(features) > 1500,
            'events': ordered[:_MAX_EVENTS]}


def _response():
    if _cache is None:
        raise HTTPException(503, 'USGS earthquakes are unavailable. Try again in a minute.',
                            headers={'Retry-After': str(_REFRESH_SECONDS)})
    now = int(wall_time() * 1000)
    return {**_cache, 'stale': _refresh_failed or now - _cache['generated_at'] > _STALE_MS
            or now - _cache['fetched_at'] > _STALE_MS, 'refresh_failed': _refresh_failed}


@router.get('/earthquakes')
async def earthquakes():
    global _cache, _next_attempt, _refresh_failed
    async with _lock:
        if monotonic() < _next_attempt:
            return _response()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                async with client.stream('GET', _USGS_URL, headers={'User-Agent': 'Apex-WorldView/1.0'}) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > _MAX_BYTES:
                            raise ValueError('Feed too large')
                        content.extend(chunk)
            _cache = _snapshot(json.loads(content), int(wall_time() * 1000))
            _refresh_failed = False
        except (httpx.HTTPError, ValueError, UnicodeError, OverflowError):
            _refresh_failed = True
        finally:
            # Bound successful refreshes and outages, including concurrent browsers.
            _next_attempt = monotonic() + _REFRESH_SECONDS
        return _response()
