"""Apex Code page and API (agent/code_studio.py): coding sessions on your Claude
and ChatGPT plans, each on its own branch.

Coding runs tools on this PC and reads your code, so every Code route needs
the owner's master token (when a dashboard token is set), reading included;
changes also need a same-site request. The page itself is public, like every
Apex page: it holds no data until the API answers.
"""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

import config
from agent import code_studio
from dashboard.companion import _check_origin

router = APIRouter()
STATIC = Path(__file__).parent / 'static'


def _owner(request: Request) -> None:
    if config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Apex Code is for the owner only (master dashboard token).')


async def _json(request: Request, limit=60000) -> dict:
    _check_origin(request)
    _owner(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, 'Too large.')
    try:
        body = json.loads(raw or b'{}')
    except ValueError:
        raise HTTPException(400, 'Expected JSON.')
    if not isinstance(body, dict):
        raise HTTPException(400, 'Expected an object.')
    return body


async def _do(call, *args, **kwargs):
    """Run in a thread (git and the plan checks take a moment); errors in plain words."""
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except code_studio.CodeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/code')
async def code_page():
    return FileResponse(STATIC / 'code.html')


@router.get('/api/code')
async def overview(request: Request):
    _owner(request)
    return await _do(code_studio.overview)


@router.post('/api/code/projects')
async def add_project(request: Request):
    body = await _json(request)
    return await _do(code_studio.add_project, body.get('path', ''), body.get('name', ''))


@router.patch('/api/code/projects/{pid}')
async def edit_project(pid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.update_project, pid, body.get('name'), body.get('checks'))


@router.post('/api/code/sessions')
async def start_session(request: Request):
    body = await _json(request)
    if not isinstance(body.get('project_id'), int):
        raise HTTPException(400, 'Choose a project.')
    return await _do(code_studio.start, body['project_id'], body.get('prompt', ''),
                     body.get('engine', 'claude'), body.get('mode', 'safe'))


@router.get('/api/code/sessions/{sid}')
async def one_session(sid: int, request: Request):
    _owner(request)
    s = await _do(code_studio.session, sid)
    try:
        s['changes'] = await asyncio.to_thread(code_studio.changes, sid)
    except code_studio.CodeError:
        s['changes'] = {'files': [], 'plus': 0, 'minus': 0}
    return s


@router.get('/api/code/sessions/{sid}/events')
async def session_events(sid: int, request: Request, after: int = 0):
    _owner(request)
    await _do(code_studio.session, sid)
    return {'events': await asyncio.to_thread(code_studio.events, sid, max(0, after))}


@router.get('/api/code/sessions/{sid}/diff')
async def file_diff(sid: int, request: Request, path: str = ''):
    _owner(request)
    return PlainTextResponse(await _do(code_studio.diff, sid, path))


@router.post('/api/code/sessions/{sid}/messages')
async def send(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.send, sid, body.get('prompt', ''), body.get('engine'), body.get('mode'))


ACTIONS = {'stop': code_studio.stop, 'undo': code_studio.undo, 'catch-up': code_studio.catch_up,
           'keep': code_studio.keep, 'discard': code_studio.discard, 'checks': code_studio.run_checks}


@router.post('/api/code/sessions/{sid}/review')
async def second_opinion(sid: int, request: Request):
    body = await _json(request)
    return await _do(code_studio.review, sid, body.get('engine'))


@router.post('/api/code/sessions/{sid}/{action}')
async def act(sid: int, action: str, request: Request):
    await _json(request)
    if action not in ACTIONS:
        raise HTTPException(404, 'Unknown action.')
    result = await _do(ACTIONS[action], sid)
    return {'stopped': result} if action == 'stop' else result
