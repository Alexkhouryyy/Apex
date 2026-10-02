"""Owner-only offline readiness, local document storage and offline chat."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import shutil
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from agent import apocalypse as mode
from dashboard.apps import owner

ROOT = Path(__file__).resolve().parents[1]
router = APIRouter()
api = APIRouter(prefix='/api/apocalypse', dependencies=[Depends(owner)])
_chat_lock = asyncio.Lock()
EXTENSIONS = {'.txt', '.md', '.pdf', '.zim', '.pmtiles'}


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
            'offline_verified': False}


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
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, 'Enter a message of up to 8,000 characters.')
    async with _chat_lock:
        try:
            if server._agent_ref:
                answer = await asyncio.to_thread(server._agent_ref.run, text, include_screenshot=False,
                                                  channel_id='apocalypse', max_iterations=5)
            else:
                answer = await asyncio.to_thread(provider.complete, 'ollama/'+mode.model_name(),
                    'You are Apex Apocalypse, a local offline assistant. Do not invent live information.', text)
        except mode.OfflineUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception:
            raise HTTPException(503, 'The local model could not answer. Check the offline launcher console.')
    return {'answer': answer, 'local': True}


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
