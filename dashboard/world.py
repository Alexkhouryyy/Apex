"""World View shell and bounded, authenticated place search.

Only explicit place searches contact Photon. No dashboard token is forwarded.
The globe, imagery and provider attribution are handled by the browser.
"""
import asyncio
import math
import time
from collections import OrderedDict
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from dashboard.world_layers import router as layers_router
from dashboard.world_flights import router as flights_router

router = APIRouter()
router.include_router(layers_router)
router.include_router(flights_router)
_cache = OrderedDict()
_search_lock = asyncio.Lock()
_last_search = 0.0
_PHOTON_URL = 'https://photon.komoot.io/api/'


@router.get('/world')
async def world_view():
    return FileResponse(Path(__file__).parent / 'static' / 'world.html')


def _places(payload):
    results = []
    features = payload.get('features', [])
    if not isinstance(features, list):
        return results
    for feature in features[:5]:
        if not isinstance(feature, dict):
            continue
        geometry, props = feature.get('geometry'), feature.get('properties')
        if not isinstance(geometry, dict) or not isinstance(props, dict):
            continue
        coords = geometry.get('coordinates')
        if geometry.get('type') != 'Point' or not isinstance(coords, list) or len(coords) < 2:
            continue
        lng, lat = coords[:2]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (lng, lat)):
            continue
        if not -180 <= lng <= 180 or not -90 <= lat <= 90:
            continue
        parts = []
        for key in ('name', 'city', 'state', 'country'):
            value = props.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in parts:
                parts.append(value.strip()[:120])
        if parts:
            results.append({'label': ', '.join(parts)[:240], 'lat': lat, 'lng': lng})
    return results


@router.get('/api/world/search')
async def search_places(q: str = Query(min_length=3, max_length=100)):
    global _last_search
    query = q.strip()
    if len(query) < 3 or any(ord(c) < 32 for c in query):
        raise HTTPException(400, 'Enter a place name with at least three characters.')
    key = query.casefold()
    async with _search_lock:
        now = time.monotonic()
        cached = _cache.get(key)
        if cached and now - cached[0] < 600:
            _cache.move_to_end(key)
            return {'results': cached[1], 'source': 'Photon / OpenStreetMap'}
        if now - _last_search < 1:
            raise HTTPException(429, 'Please wait a moment before searching again.')
        _last_search = now
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                response = await client.get(_PHOTON_URL, params={'q': query, 'limit': 5},
                                            headers={'User-Agent': 'Apex-WorldView/1.0'})
                response.raise_for_status()
                if len(response.content) > 262144:
                    raise ValueError('Place response too large')
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError('Invalid place response')
                results = _places(payload)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'Place search is unavailable. Try coordinates or a saved city.') from None
        _cache[key] = (time.monotonic(), results)
        _cache.move_to_end(key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return {'results': results, 'source': 'Photon / OpenStreetMap'}
