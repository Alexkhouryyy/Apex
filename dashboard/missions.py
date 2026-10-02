"""Missions page and API (agent/missions.py). Starting or resuming a mission
lets Apex work unattended with write and shell tools, so those need the
master dashboard token; anyone signed in can watch."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
from agent import missions
from dashboard.companion import _check_origin

router = APIRouter()
STATIC = Path(__file__).parent / 'static'


def _owner(request: Request):
    if config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Only the owner (master dashboard token) can start or resume missions.')


async def _json(request: Request, limit=40000):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, 'Too large.')
    try:
        return json.loads(raw or b'{}')
    except ValueError:
        raise HTTPException(400, 'Expected JSON.')


def _agent():
    from dashboard import server
    if not server._agent_ref:
        raise HTTPException(503, 'Apex is still starting.')
    return server._agent_ref


@router.get('/missions')
async def page():
    return FileResponse(STATIC / 'missions.html')


@router.get('/api/missions')
async def listing():
    return {'missions': missions.recent()}


@router.get('/api/missions/{mid}')
async def one(mid: str):
    m = missions.get(mid)
    if not m:
        raise HTTPException(404, 'Mission not found.')
    return m


@router.post('/api/missions')
async def create(request: Request):
    _check_origin(request)
    _owner(request)
    body = await _json(request)
    try:
        return missions.create(body, _agent())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/missions/{mid}/{action}')
async def control(mid: str, action: str, request: Request):
    _check_origin(request)
    if action not in ('pause', 'stop', 'resume'):
        raise HTTPException(404, 'Unknown action.')
    if action == 'resume':
        _owner(request)
    body = await _json(request, 2000) if action == 'resume' else {}
    try:
        if action == 'resume':
            return missions.resume(mid, _agent(), extra_rounds=body.get('extra_rounds', 0),
                                   extra_budget=body.get('extra_budget', 0.0))
        return getattr(missions, action)(mid)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
