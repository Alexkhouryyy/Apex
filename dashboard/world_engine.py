"""Apex-owned lifecycle and authenticated gateway for the pinned GEV engine."""
from __future__ import annotations

import atexit
import asyncio
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'integrations' / 'gods-eye-view'
PREFIX = '/world/engine/'
COOKIE = 'apex_world_session'
router = APIRouter()
_sessions: dict[str, tuple[float, str]] = {}
_session_lock = threading.Lock()


def _credential_valid(value: str, configured: str) -> bool:
    if not configured:
        return True
    if value and hmac.compare_digest(value, configured):
        return True
    try:
        from agent import access_tokens
        return bool(value) and access_tokens.verify(value)
    except Exception:
        return False


def authenticate(request: Request, configured: str) -> bool:
    """Validate revocation on every request; never pass an Apex credential downstream."""
    auth = request.headers.get('authorization', '')
    value = auth[7:] if auth.startswith('Bearer ') else ''
    if not value:
        with _session_lock:
            session = _sessions.get(request.cookies.get(COOKIE, ''))
        if session and session[0] > time.monotonic():
            value = session[1]
    if not _credential_valid(value, configured):
        return False
    request.state.is_master = bool(configured and value and hmac.compare_digest(value, configured))
    return True


def same_origin(request: Request) -> None:
    origin = request.headers.get('origin')
    expected = str(request.base_url).rstrip('/')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin != expected):
        raise HTTPException(403, 'World View requests must come from this Apex window.')
    if request.method not in ('GET', 'HEAD') and not origin:
        # Non-browser clients can use an explicit bearer credential instead.
        if not request.headers.get('authorization', '').startswith('Bearer '):
            raise HTTPException(403, 'An Origin or bearer credential is required.')


class EngineRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.port = None
        self.secret = secrets.token_urlsafe(32)
        self.log = None
        self.starts = []

    def status(self):
        provenance = ENGINE / 'APEX_UPSTREAM.json'
        metadata = json.loads(provenance.read_text()) if provenance.exists() else {}
        return {'installed': (ENGINE / 'node_modules/vite/package.json').exists(),
                'running': bool(self.process and self.process.poll() is None),
                'revision': metadata.get('revision'), 'repository': metadata.get('repository'),
                'log': str(ROOT / '.mcp-runtime' / 'world-engine.log')}

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.port
            node = shutil.which('node')
            if not node or not self.status()['installed']:
                raise RuntimeError('World engine dependencies are missing. Run Setup-Apex-World.cmd.')
            now = time.monotonic()
            self.starts = [t for t in self.starts if now - t < 120]
            if len(self.starts) >= 3:
                raise RuntimeError('World engine stopped repeatedly. Check world-engine.log before restarting.')
            self.starts.append(now)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            env = dict(os.environ)
            env.update(APEX_ENGINE_SECRET=self.secret, APEX_ENGINE_PORT=str(self.port),
                       APEX_PARENT_PID=str(os.getpid()))
            # Provider credentials come from Apex's existing environment; the private
            # integration store is loaded by GEV itself. No dashboard token is needed.
            env.pop('DASHBOARD_TOKEN', None)
            log_path = ROOT / '.mcp-runtime' / 'world-engine.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = log_path.open('a', encoding='utf-8')
            self.process = subprocess.Popen([node, str(ENGINE / 'apex-server.mjs')], cwd=ENGINE,
                env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and self.process.poll() is None:
                    try:
                        response = client.get(f'http://127.0.0.1:{self.port}/apex-health',
                                              headers={'x-apex-engine-secret': self.secret})
                        if response.status_code == 200 and response.json().get('engine') == 'gods-eye-view':
                            return self.port
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(.15)
            self.stop()
            raise RuntimeError('World engine could not start. Check .mcp-runtime/world-engine.log.')

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()
            self.log = None


runtime = EngineRuntime()
atexit.register(runtime.stop)


@router.get('/api/world/engine/status')
async def engine_status():
    return runtime.status()


@router.post('/api/world/engine/session')
async def engine_session(request: Request):
    same_origin(request)
    auth = request.headers.get('authorization', '')
    value = auth[7:] if auth.startswith('Bearer ') else ''
    sid = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _session_lock:
        for key in list(_sessions):
            if _sessions[key][0] <= now:
                del _sessions[key]
        if len(_sessions) >= 128:
            raise HTTPException(429, 'Too many World View sessions. Close older windows.')
        _sessions[sid] = (now + 8 * 3600, value)
    response = JSONResponse({'url': PREFIX, 'expires_in': 8 * 3600})
    response.set_cookie(COOKIE, sid, max_age=8 * 3600, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path=PREFIX)
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/world/basic')
async def basic_world():
    return FileResponse(Path(__file__).parent / 'static/world.html')


@router.post(PREFIX + 'apex/speak')
async def engine_speak(request: Request):
    same_origin(request)
    from dashboard.server import speak_endpoint
    return await speak_endpoint(request)


@router.api_route(PREFIX + '{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'DELETE'])
async def engine_gateway(request: Request, path: str):
    same_origin(request)
    if path.startswith('api/setup/') and request.method != 'GET':
        import config
        if (config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False)) or (
                not request.client or request.client.host not in ('127.0.0.1', '::1', 'localhost', 'testclient')):
            raise HTTPException(403, 'Only the local Apex owner can change provider keys.')
    # Never offer the dependency's source tree, credentials, or Vite filesystem
    # escape hatch. Runtime modules and assets remain available inside this root.
    parts = path.replace('\\', '/').split('/')
    if (any(p in ('.', '..') or p.startswith('.') or p == 'ENVIRONMENT' for p in parts)
            or any(p.startswith('@fs') for p in parts) or path.endswith(('.pem', '.key', '.crt'))):
        raise HTTPException(404, 'Not found')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(413, 'World View request is too large.')
    try:
        port = await asyncio.to_thread(runtime.start)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    target = httpx.URL(f'http://127.0.0.1:{port}{PREFIX}{path}', query=request.url.query.encode())
    headers = {'x-apex-engine-secret': runtime.secret}
    for key in ('content-type', 'accept', 'range', 'if-none-match'):
        if request.headers.get(key):
            headers[key] = request.headers[key]
    # The outer origin was checked above. GEV's local provider-settings admission
    # sees its own loopback origin; no cookies/authorization/proxy headers cross.
    if request.headers.get('origin'):
        headers['origin'] = f'http://127.0.0.1:{port}'
    client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(90, connect=5))
    try:
        response = await client.send(client.build_request(request.method, target,
                                     headers=headers, content=bytes(raw)), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, 'World engine is unavailable. Retry or check world-engine.log.') from None
    async def cleanup():
        await response.aclose()
        await client.aclose()
    kept = {key: response.headers[key] for key in ('content-type', 'etag', 'content-range', 'accept-ranges')
            if key in response.headers}
    kept.update({'Cache-Control': 'no-store', 'X-Frame-Options': 'SAMEORIGIN',
                 'Content-Security-Policy': "frame-ancestors 'self'", 'X-Content-Type-Options': 'nosniff'})
    return StreamingResponse(response.aiter_bytes(), status_code=response.status_code,
                             headers=kept, background=BackgroundTask(cleanup))
