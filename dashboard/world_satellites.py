"""CelesTrak station OMMs; persistent cooldown and explicit source-error recovery."""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from time import time as wall_time

import httpx
from fastapi import APIRouter, HTTPException
from dashboard.world_layers import _number

router = APIRouter(prefix='/api/world/layers')
_URL = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=JSON'
_MAX_BYTES = 512 * 1024
_INTERVAL_MS = 2 * 60 * 60 * 1000
_lock = asyncio.Lock()


def _cache_file():
    from agent import longterm
    return Path(os.environ.get('APEX_WORLD_STATIONS_CACHE') or
                str(Path(longterm.DB_PATH).expanduser().with_suffix('.world-stations.json')))


def _snapshot(payload, fetched_at):
    if not isinstance(payload, list):
        raise ValueError('Invalid station collection')
    records = {}
    fields = {'MEAN_MOTION': (0.1, 20), 'ECCENTRICITY': (0, 0.99), 'INCLINATION': (0, 180),
              'RA_OF_ASC_NODE': (0, 360), 'ARG_OF_PERICENTER': (0, 360), 'MEAN_ANOMALY': (0, 360),
              'BSTAR': (-1, 1), 'MEAN_MOTION_DOT': (-10, 10), 'MEAN_MOTION_DDOT': (-10, 10)}
    for raw in payload[:256]:
        if not isinstance(raw, dict):
            continue
        identity, name, epoch = raw.get('NORAD_CAT_ID'), raw.get('OBJECT_NAME'), raw.get('EPOCH')
        if (not _number(identity, 1, 999999999) or int(identity) != identity
                or not isinstance(name, str) or not name.strip() or not isinstance(epoch, str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z?', epoch)):
            continue
        try:
            stamp = datetime.fromisoformat(epoch.rstrip('Z')).replace(tzinfo=timezone.utc)
            epoch_ms = int(stamp.timestamp() * 1000)
        except (ValueError, OverflowError):
            continue
        if not _number(epoch_ms, 946684800000, fetched_at + 86400000):
            continue
        if any(raw.get(k, v) != v for k, v in {'CENTER_NAME': 'EARTH', 'REF_FRAME': 'TEME',
                                             'TIME_SYSTEM': 'UTC', 'MEAN_ELEMENT_THEORY': 'SGP4'}.items()):
            continue
        if raw.get('EPHEMERIS_TYPE') != 0 or any(not _number(raw.get(k), *bounds) for k, bounds in fields.items()):
            continue
        omm = {k: raw[k] for k in fields}
        omm.update({'NORAD_CAT_ID': int(identity), 'OBJECT_NAME': name.strip()[:120],
                    'EPOCH': stamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                    'EPHEMERIS_TYPE': 0, 'CLASSIFICATION_TYPE': 'U'})
        record = {'id': str(int(identity)), 'name': name.strip()[:120], 'epoch_at': epoch_ms, 'omm': omm}
        if record['id'] not in records or records[record['id']]['epoch_at'] < epoch_ms:
            records[record['id']] = record
    if payload and not records:
        raise ValueError('No valid station elements')
    ordered = sorted(records.values(), key=lambda r: (r['id'] != '25544', r['id']))
    return {'source': 'CelesTrak', 'source_url': _URL, 'group': 'stations', 'format': 'OMM JSON',
            'fetched_at': fetched_at, 'refresh_after_seconds': 7200,
            'truncated': len(ordered) > 64 or len(payload) > 256, 'satellites': ordered[:64]}


def _read():
    path = _cache_file()
    if not path.exists():
        return {'snapshot': None, 'next_attempt': 0, 'paused': False, 'failed': False}
    if path.stat().st_size > _MAX_BYTES:
        raise ValueError('Station cache too large')
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or not _number(data.get('next_attempt'), 0, wall_time() * 1000 + 86400000)
            or not isinstance(data.get('paused'), bool) or not isinstance(data.get('failed'), bool)):
        raise ValueError('Invalid station cache')
    snapshot = data.get('snapshot')
    if snapshot is not None:
        fetched = snapshot.get('fetched_at') if isinstance(snapshot, dict) else None
        if not _number(fetched, 946684800000, wall_time() * 1000 + 30000):
            raise ValueError('Invalid station cache time')
        data['snapshot'] = _snapshot([r['omm'] for r in snapshot['satellites']], int(fetched))
        data['snapshot']['truncated'] = data['snapshot']['truncated'] or bool(snapshot.get('truncated'))
    return data


def _write(data):
    path = _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def _response(data):
    snapshot = data['snapshot']
    if snapshot is None:
        raise HTTPException(503, 'Station source unavailable or paused. Use Retry source after the cooldown.',
                            headers={'Retry-After': str(max(1, int((data['next_attempt'] - wall_time() * 1000) / 1000)))})
    return {**snapshot, 'stale': data['failed'] or wall_time() * 1000 - snapshot['fetched_at'] > 21600000,
            'refresh_failed': data['failed'], 'source_paused': data['paused'],
            'next_attempt_at': data['next_attempt']}


async def _load(retry=False):
    async with _lock:
        try:
            data = _read()
            if retry:
                data['paused'] = False
                _write(data)
            if data['paused'] or wall_time() * 1000 < data['next_attempt']:
                return _response(data)
            # Persist the cooldown before contacting the provider so a restart cannot hammer it.
            data['next_attempt'] = int(wall_time() * 1000) + _INTERVAL_MS
            _write(data)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, 'Station cache unavailable. Check storage before retrying.')
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                async with client.stream('GET', _URL, headers={
                    'User-Agent': 'Apex-WorldView/1.0 (+https://github.com/Alexkhouryyy/Apex/issues)'}) as response:
                    # Redirects and every HTTP error stop automatic source requests.
                    if response.status_code != 200:
                        raise ValueError('Station provider returned an error')
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > _MAX_BYTES:
                            raise ValueError('Station feed too large')
                        content.extend(chunk)
            data['snapshot'] = _snapshot(json.loads(content), int(wall_time() * 1000))
            data['failed'] = data['paused'] = False
        except (httpx.HTTPError, ValueError, UnicodeError, OverflowError):
            data['failed'] = data['paused'] = True
        try:
            _write(data)
        except OSError:
            # The pre-request cooldown is already saved. Do not call the provider again.
            raise HTTPException(503, 'Station snapshot could not be saved. Check storage.')
        return _response(data)


@router.get('/satellites')
async def satellites():
    return await _load()


@router.post('/satellites/retry')
async def retry_satellites():
    return await _load(retry=True)
