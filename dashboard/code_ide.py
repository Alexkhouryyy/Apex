"""Owner-only, session-scoped HTTP/WebSocket gateway to the private IDE."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

import config
from agent import code_ide, code_studio
from dashboard.code import _owner, _json, _do

router = APIRouter()
COOKIE = 'apex_code_ide'
PREFIX = '/code/ide/'
_grants = {}
_lock = threading.Lock()
HOP = {'connection', 'upgrade', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'host', 'cookie', 'authorization', 'x-apex-ide-secret'}


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(request, configured: str) -> bool:
    match = re.match(r'^/code/ide/(\d+)/', request.url.path)
    if not match:
        return False
    sid = int(match[1])
    runtime = code_ide.get(sid)
    if not runtime:
        return False
    with _lock:
        grant = _grants.get(request.cookies.get(COOKIE, ''))
    if not grant or grant[0] < time.monotonic() or grant[1] != sid or grant[2] != runtime.secret:
        return False
    if not hmac.compare_digest(grant[3], _fingerprint(configured)):
        return False
    request.state.is_master = True
    return True


def same_origin(request):
    origin = request.headers.get('origin')
    expected = f'{request.url.scheme if request.url.scheme in ("http", "https") else "https" if request.url.scheme == "wss" else "http"}://{request.url.netloc}'
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != expected):
        raise HTTPException(403, 'Open the IDE from your Apex window.')
    # WebSocket credentials are cookies; browsers always provide an Origin.
    if isinstance(request, WebSocket) and origin != expected:
        raise HTTPException(403, 'Open the IDE from your Apex window.')


@router.get('/api/code/ide/status')
async def installation(request: Request):
    _owner(request)
    return code_ide.installation()


@router.post('/api/code/sessions/{sid}/ide')
async def launch(sid: int, request: Request):
    await _json(request, limit=2000)
    status = await _do(code_ide.start, sid)
    runtime = code_ide.get(sid)
    key = secrets.token_urlsafe(32)
    with _lock:
        now = time.monotonic()
        for old, grant in list(_grants.items()):
            if grant[0] <= now or not code_ide.get(grant[1]):
                del _grants[old]
        if len(_grants) >= 128:
            raise HTTPException(429, 'Too many IDE windows. Close older ones first.')
        _grants[key] = (now + 8*3600, sid, runtime.secret, _fingerprint(config.DASHBOARD_TOKEN))
    response = JSONResponse(status)
    response.set_cookie(COOKIE, key, max_age=8*3600, httponly=True, secure=request.url.scheme=='https',
                        samesite='strict', path=f'{PREFIX}{sid}/')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/api/code/sessions/{sid}/ide')
async def status(sid: int, request: Request):
    _owner(request)
    await _do(code_studio.session, sid)
    return code_ide.status(sid)


@router.delete('/api/code/sessions/{sid}/ide')
async def close(sid: int, request: Request):
    await _json(request, limit=2000)
    # Closing releases resource usage even when an agent/check is running.
    await _do(code_ide.stop, sid)
    return {'running': False}


@router.post('/api/code/sessions/{sid}/ide-checkpoint')
async def checkpoint(sid: int, request: Request):
    await _json(request, limit=2000)
    return await _do(code_ide.checkpoint, sid)


def _runtime(sid, request):
    if not authenticate(request, config.DASHBOARD_TOKEN):
        raise HTTPException(401, 'Open Full IDE from the signed-in Apex Code window.')
    same_origin(request)
    runtime = code_ide.get(sid)
    if not runtime:
        raise HTTPException(410, 'This IDE session has ended.')
    return runtime


@router.api_route('/code/ide/{sid}/{path:path}', methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])
async def proxy(sid: int, path: str, request: Request):
    runtime = _runtime(sid, request)
    if path.startswith('webview/') and not getattr(request.state, 'isolated_webview', False):
        raise HTTPException(403, 'IDE webviews use their isolated browser origin.')
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120, connect=5))
    headers = {k:v for k,v in request.headers.items() if k.lower() not in HOP and not k.lower().startswith('x-forwarded-')}
    headers['x-apex-ide-secret'] = runtime.secret
    headers['cookie'] = 'theia-connection-token=' + runtime.connection_token
    if 'origin' in headers:
        headers['origin'] = f'http://127.0.0.1:{runtime.port}'
    headers['x-forwarded-host'] = request.url.netloc
    headers['x-forwarded-proto'] = request.url.scheme
    # Construct a URL object: encoded ?/# in an asset name must stay in the path.
    raw_path = request.scope['raw_path'][len(f'{PREFIX}{sid}'.encode()):]
    query = request.scope['query_string']
    target = httpx.URL(f'http://127.0.0.1:{runtime.port}').copy_with(
        raw_path=raw_path + (b'?'+query if query else b''))
    try:
        upstream = await client.send(client.build_request(request.method, target, headers=headers,
                                     content=request.stream()), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, 'The IDE stopped responding. Close and reopen Full IDE.') from exc
    outgoing = {k:v for k,v in upstream.headers.items() if k.lower() not in HOP|{'content-encoding','content-length','set-cookie'}}
    outgoing['Cache-Control'] = 'no-store'
    location = outgoing.get('location')
    if location:
        parsed = urlsplit(location)
        if not parsed.netloc or parsed.netloc == f'127.0.0.1:{runtime.port}':
            outgoing['location'] = f'{PREFIX}{sid}/'+parsed.path.lstrip('/')+('?' + parsed.query if parsed.query else '')
    async def cleanup():
        await upstream.aclose()
        await client.aclose()
    return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code, headers=outgoing,
                             background=BackgroundTask(cleanup))


@router.websocket('/code/ide/{sid}/{path:path}')
async def socket(sid: int, path: str, websocket: WebSocket):
    try:
        runtime = _runtime(sid, websocket)
    except HTTPException:
        await websocket.close(code=4403)
        return
    query = ('?' + websocket.url.query) if websocket.url.query else ''
    try:
        async with websockets.connect(f'ws://127.0.0.1:{runtime.port}/{path}{query}',
                additional_headers={'x-apex-ide-secret':runtime.secret,
                                    'cookie':'theia-connection-token='+runtime.connection_token},
                origin=f'http://127.0.0.1:{runtime.port}', max_size=None,
                open_timeout=10) as upstream:
            await websocket.accept()
            async def incoming():
                while True:
                    message = await websocket.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    await upstream.send(message.get('bytes') if message.get('bytes') is not None else message['text'])
            async def outgoing():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            async def revocation():
                while True:
                    await asyncio.sleep(5)
                    if not authenticate(websocket, config.DASHBOARD_TOKEN):
                        await websocket.close(code=4401)
                        return
            tasks = [asyncio.create_task(incoming()), asyncio.create_task(outgoing()),
                     asyncio.create_task(revocation())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, websockets.WebSocketException, WebSocketDisconnect):
        pass
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
