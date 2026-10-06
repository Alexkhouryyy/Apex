"""Owner-only offline readiness, local document storage and offline chat."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import time

import httpx
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from agent import apocalypse as mode, offline_sessions, continuity, board_workspaces
from dashboard.apps import owner

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()
api = APIRouter(prefix='/api/apocalypse', dependencies=[Depends(owner)])
_chat_lock = asyncio.Lock()
_log = logging.getLogger(__name__)
EXTENSIONS = {'.txt', '.md', '.pdf', '.zim', '.pmtiles'}


async def project_operation(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except board_workspaces.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        _log.warning('Offline project storage unavailable: %s', type(exc).__name__)
        raise HTTPException(503, 'Project storage is unavailable. Your draft was kept; reload before retrying.') from exc


@api.get('/session')
async def saved_session(workspace_id: str | None = None):
    return await project_operation(offline_sessions.session, workspace_id)


@api.post('/projects/{workspace_id}')
async def save_handoff(workspace_id: str, request: Request):
    from dashboard.home import body
    data = await body(request)
    return await project_operation(continuity.save_project, workspace_id, data.get('data'), data.get('revision'))


@router.get('/apocalypse')
def shell():
    return FileResponse(ROOT / 'dashboard/static/apocalypse.html', headers={'Cache-Control': 'no-store'})


async def local_json(client, url):
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


def documents():
    root = mode.library_root() / 'documents'
    rows = []
    if root.is_dir():
        for item in sorted(root.iterdir()):
            if item.is_file() and not item.is_symlink() and item.suffix.lower() in EXTENSIONS:
                rows.append({'name': item.name, 'bytes': item.stat().st_size})
                if len(rows) >= 500:
                    break
    return rows


def preparation():
    path = mode.library_root()/'english-worldwide-plan.json'
    if not path.is_file():
        return {'exists': False}
    try:
        if path.stat().st_size > 1024*1024:
            raise ValueError('Plan is too large')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('profile') != 'english-worldwide':
            raise ValueError('Unexpected profile')
        jobs = data['jobs']
        return {'exists': True, 'profile': 'English + worldwide maps',
                'downloaded': sum(row.get('status') == 'downloaded' and row.get('checksum_verified') is True for row in jobs),
                'total': len(jobs), 'bytes': sum(int(row['bytes']) for row in jobs),
                'needs_attention': sum(row.get('status') == 'needs_attention' for row in jobs),
                'remaining_setup': data['remaining_setup'], 'updated_at': data.get('updated_at'),
                'offline_verified': False}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'exists': True, 'error': 'The saved download plan needs review. Your library files were kept.'}


@api.get('/status')
async def status():
    import config
    try:
        voice_url = mode.loopback_url(config.VOICEBOX_URL)+'/health'
    except mode.OfflineUnavailable:
        voice_url = 'http://127.0.0.1:17494/health'
    async with httpx.AsyncClient(trust_env=False, timeout=3, follow_redirects=False) as client:
        tags, nomad, voice = await asyncio.gather(
            local_json(client, mode.ollama_url()+'/api/tags'),
            local_json(client, 'http://127.0.0.1:8080/api/health'),
            local_json(client, voice_url))
    models = [row.get('name') or row.get('model') for row in (tags or {}).get('models', []) if mode.is_local_model(row)]
    selected = mode.model_name()
    disk = mode.library_root()
    try:
        free = shutil.disk_usage(disk if disk.exists() else disk.parent).free
    except OSError:
        free = None
    provenance = json.loads((ROOT/'integrations/project-nomad/UPSTREAM.json').read_text())
    return {'active': mode.enabled(), 'model': {'selected': selected, 'downloaded': selected in models,
            'available': models, 'server_ready': tags is not None, 'response_verified': False},
            'storage': {'path': str(disk), 'free_bytes': free}, 'documents': documents(),
            'nomad': {'ready': isinstance(nomad, dict) and nomad.get('status') == 'ok',
                      'url': 'http://127.0.0.1:8080', 'revision': provenance['revision'],
                      'docker_available': bool(shutil.which('docker'))},
            'voice': {'server_ready': isinstance(voice, dict), 'audio_verified': False},
            'internet_features': 'paused' if mode.enabled() else 'normal',
            'offline_verified': False, 'preparation': preparation()}


@api.post('/chat')
async def chat(request: Request):
    if not mode.enabled():
        raise HTTPException(409, 'Start-Apex-Apocalypse.cmd opens the separate offline session.')
    from dashboard import server
    from agent import provider
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 32000:
            raise HTTPException(413, 'Message is too long.')
    try:
        data = json.loads(raw)
        text = data.get('message', '').strip()
        if not isinstance(text, str) or not text or len(text) > 8000:
            raise ValueError()
        workspace_id = data.get('workspace_id')
        if workspace_id is not None and not isinstance(workspace_id, str):
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, 'Enter a message of up to 8,000 characters.')
    async with _chat_lock:
        if not server._agent_ref:
            # A single completion cannot restore project history or execute the
            # document workflow. Do not report the platform as ready without it.
            raise HTTPException(503, 'The offline agent is not ready. Keep the launcher running and refresh readiness.')
        saved = await project_operation(offline_sessions.session, workspace_id, create=True)
        try:
            answer = await asyncio.to_thread(server._agent_ref.run, text, include_screenshot=False,
                                              channel_id='apocalypse:'+str(saved['thread_id']), max_iterations=5)
            if not isinstance(answer, str) or not answer.strip():
                raise provider.LocalResponseIncomplete(
                    'The local model returned no final answer. Ask for one part of the question at a time.')
        except (mode.OfflineUnavailable, provider.LocalResponseIncomplete) as exc:
            _log.warning('Local chat unavailable: %s', type(exc).__name__)
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            # Provider exceptions can contain prompts, filenames or credentials.
            # Log fixed categories and status only, never the raw exception.
            from openai import APIConnectionError, APITimeoutError, APIStatusError
            status = getattr(exc, 'status_code', None) if isinstance(exc, APIStatusError) else None
            if isinstance(exc, APITimeoutError):
                category = 'timeout'
                detail = 'The local model took too long to answer. Try a smaller part of the question.'
            elif isinstance(exc, APIConnectionError):
                category = 'connection'
                detail = 'The local model connection stopped. Keep the offline launcher running and refresh readiness.'
            elif status == 400:
                category = 'request_rejected'
                detail = 'The local model rejected this request (400). Restart the offline session and try again.'
            elif status is not None and status >= 500:
                category = 'model_server'
                detail = 'The local model server failed while answering. Check the offline launcher and available memory.'
            else:
                category = 'unexpected'
                detail = 'Local chat failed. The offline launcher shows the failure type; restart the session and try again.'
            _log.warning('Local chat failed: type=%s category=%s status=%s', type(exc).__name__, category, status)
            raise HTTPException(503, detail) from exc
    return {'answer': answer, 'local': True, 'thread_id': saved['thread_id'],
            'workspace_id': saved['project']['id'], 'saved': True}


def document_path(name):
    if not re.fullmatch(r'[\w .()\-]{1,160}', name) or name in {'.', '..'}:
        raise HTTPException(400, 'Use a plain filename without folders.')
    root = (mode.library_root()/'documents').resolve()
    candidate = root/name
    target = candidate.resolve()
    if target.parent != root or target.suffix.lower() not in EXTENSIONS or candidate.is_symlink():
        raise HTTPException(400, 'Choose a text, Markdown, PDF, ZIM or PMTiles file.')
    return target


@api.post('/documents/{name}')
async def upload(name: str, request: Request):
    target = document_path(name)
    # Large archives are copied directly to D: or downloaded through NOMAD;
    # keep browser uploads bounded and never overwrite existing documents.
    if target.suffix.lower() not in {'.txt', '.md', '.pdf'}:
        raise HTTPException(400, 'Copy large ZIM and PMTiles archives into the library folder.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8*1024*1024:
            raise HTTPException(413, 'Upload up to 8 MB; copy larger files directly to the library folder.')
    if not raw:
        raise HTTPException(400, 'The document is empty.')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open('xb') as output:
            output.write(raw)
    except FileExistsError:
        raise HTTPException(409, 'That filename already exists. Your saved file was kept.')
    return {'saved': name, 'bytes': len(raw)}


@api.get('/documents/{name}')
def download(name: str):
    target = document_path(name)
    if not target.is_file():
        raise HTTPException(404, 'Document not found.')
    return FileResponse(target, filename=target.name, media_type='application/octet-stream',
                        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


router.include_router(api)
