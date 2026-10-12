"""Reverse page and API (agent/reverse.py): open software you own and see how it
works, through REA's read-only tools.

Reverse engineering reads files on this PC and runs REA, so every route is the
owner's: master token when a dashboard token is set, and a same-site request for
anything that opens or closes a session. The page itself holds no data until the
API answers, like every Apex page. Use it only on software you own or are allowed
to examine.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from agent import reverse
from dashboard.code import _owner, _json, _do

router = APIRouter()
STATIC = Path(__file__).parent / 'static'


@router.get('/reverse')
async def page():
    return FileResponse(STATIC / 'reverse.html')


@router.get('/api/reverse')
async def index(request: Request):
    _owner(request)
    return {'sessions': await _do(reverse.sessions), 'ready': reverse.rea_ready()}


@router.post('/api/reverse/open')
async def open_file(request: Request):
    body = await _json(request, limit=8000)
    return await _do(reverse.open_binary, str(body.get('path') or ''))


@router.get('/api/reverse/sessions/{sid}')
async def one(sid: int, request: Request):
    _owner(request)
    return await _do(reverse.session, sid)


@router.get('/api/reverse/sessions/{sid}/overview')
async def overview(sid: int, request: Request):
    _owner(request)
    return await _do(reverse.overview, sid)


@router.get('/api/reverse/sessions/{sid}/functions')
async def functions(sid: int, request: Request, q: str = '', limit: int = reverse.LIST_CAP):
    _owner(request)
    return await _do(reverse.functions, sid, q, limit)


@router.get('/api/reverse/sessions/{sid}/strings')
async def strings(sid: int, request: Request, q: str = '', limit: int = reverse.LIST_CAP):
    _owner(request)
    return await _do(reverse.strings, sid, q, limit)


@router.get('/api/reverse/sessions/{sid}/function')
async def function(sid: int, request: Request, address: str = ''):
    _owner(request)
    return await _do(reverse.function, sid, address)


@router.post('/api/reverse/sessions/{sid}/close')
async def close(sid: int, request: Request):
    await _json(request, limit=2000)
    return await _do(reverse.close, sid)
