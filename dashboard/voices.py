"""Voices page: record a voice (yours, Celine's, anyone who agrees), check it,
and save it where Apex's own Qwen voice servers find it (scripts/voice_library.py).

Saving and removing need the master dashboard token (a per-device token can
listen, not make voices of people), and a statement that the speaker agreed.
A removed voice is moved to a .trash folder, not deleted.
"""
import asyncio
import base64
import binascii
import json
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
from dashboard.companion import _check_origin
from scripts import voice_library

router = APIRouter()
STATIC = Path(__file__).parent / 'static'
_busy = threading.Lock()
MAX_BODY = 17_000_000                 # a 12 MB recording, base64-encoded, plus the text


def _legacy():
    from scripts.qwen_server import TRANSCRIPT
    return dict(legacy=Path.home() / 'Downloads' / 'celine.ogg', legacy_transcript=TRANSCRIPT)


def _owner(request: Request):
    if config.DASHBOARD_TOKEN and not getattr(request.state, 'is_master', False):
        raise HTTPException(403, 'Only the owner (master dashboard token) can add or remove voices.')


async def _body(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise HTTPException(413, 'The recording is too large; keep it under a minute and a half.')
    try:
        body = json.loads(raw)
        audio = base64.b64decode(body['audio'], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise HTTPException(400, 'Send the recording and its transcript.')
    transcript = body.get('transcript', '')
    if not isinstance(transcript, str) or len(transcript) > voice_library.MAX_TRANSCRIPT:
        raise HTTPException(400, f'The transcript must be text of at most {voice_library.MAX_TRANSCRIPT} characters.')
    return dict(body, audio=audio, transcript=' '.join(transcript.split()))


def _listen(audio: bytes) -> str | None:
    """What local Whisper hears in the recording, or None when it cannot listen."""
    try:
        from voice.browser_stt import transcribe
        return transcribe(audio, 'voice.webm', 'local')
    except Exception:
        return None


def _measure(body: dict, listen=None) -> tuple:
    from voice import voice_check
    audio = voice_check.trim(voice_check.decode(body['audio']))
    heard = (listen or _listen)(voice_check.to_wav(audio))
    report = voice_check.analyse(audio, body['transcript'], heard)
    if heard is None:
        report['warnings'].append("Apex couldn't listen to the recording to check the words, so make sure the text is exactly what you said.")
    return audio, report


async def _measured(body: dict) -> tuple:
    if not _busy.acquire(blocking=False):
        raise HTTPException(429, 'Apex is checking another recording. Try again in a moment.')
    try:
        return await asyncio.get_running_loop().run_in_executor(None, _measure, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        _busy.release()


@router.get('/voices')
async def voices_page():
    return FileResponse(STATIC / 'voices.html')


@router.get('/api/voices')
async def list_voices():
    from voice import voicebox
    voices = voice_library.discover(**_legacy())
    health = None
    try:
        import httpx
        async with httpx.AsyncClient(base_url=voicebox.base_url(), trust_env=False, timeout=httpx.Timeout(3, connect=2)) as c:
            r = await c.get('/health')
            health = r.json() if r.status_code == 200 else None
    except Exception:
        health = None
    if isinstance(health, dict) and health.get('service') == 'apex-qwen':
        server = {'kind': 'apex', 'streaming': bool(health.get('streaming')), 'voices': health.get('voices') or []}
    elif isinstance(health, dict):
        server = {'kind': 'voicebox-app'}
    else:
        server = {'kind': 'offline'}
    return {'dir': str(voice_library.voices_dir()), 'server': server,
            'voices': [{'id': v.id, 'name': v.name, 'words': len(v.transcript.split()),
                        'removable': v.folder} for v in voices]}


@router.post('/api/voices/check')
async def check_voice(request: Request):
    _check_origin(request)
    _owner(request)
    _, report = await _measured(await _body(request))
    return report


@router.post('/api/voices')
async def save_voice(request: Request):
    _check_origin(request)
    _owner(request)
    body = await _body(request)
    name = ' '.join(str(body.get('name') or '').split())[:40]
    vid = voice_library.slug(name)
    if not name or not voice_library.ID.match(vid or '-'):
        raise HTTPException(400, 'Give the voice a name, like Alex.')
    if body.get('consent') is not True:
        raise HTTPException(400, 'Confirm this is your voice, or that the person agreed to have it cloned.')
    root = voice_library.voices_dir()
    folder = root / vid
    if folder.exists() and body.get('replace') is not True:
        raise HTTPException(409, f'There is already a voice called {name}. Tick "Replace it" to record it again.')
    audio, report = await _measured(body)
    if report['problems']:
        raise HTTPException(422, ' '.join(report['problems']))
    from voice import voice_check
    staging = root / f'.new-{vid}-{time.time_ns()}'
    staging.mkdir(parents=True)
    (staging / 'reference.wav').write_bytes(voice_check.to_wav(audio))
    (staging / 'transcript.txt').write_text(body['transcript'] + '\n', encoding='utf-8')
    (staging / 'voice.json').write_text(json.dumps({
        'name': name, 'created': time.strftime('%Y-%m-%d %H:%M'), 'consent': 'confirmed by the owner when recorded',
        'checks': {k: report[k] for k in ('seconds', 'speech_db', 'snr_db', 'match')}}, indent=1), encoding='utf-8')
    if folder.exists():
        _trash(folder)
    staging.rename(folder)
    return {'id': vid, 'name': name, 'report': report}


def _trash(folder: Path):
    bin_ = folder.parent / '.trash'
    bin_.mkdir(exist_ok=True)
    shutil.move(str(folder), str(bin_ / f'{folder.name}-{time.strftime("%Y%m%d-%H%M%S")}-{time.time_ns() % 10**6}'))


@router.delete('/api/voices/{voice_id}')
async def remove_voice(voice_id: str, request: Request):
    _check_origin(request)
    _owner(request)
    if not voice_library.ID.match(voice_id):
        raise HTTPException(400, 'Unknown voice.')
    folder = voice_library.voices_dir() / voice_id
    if not folder.is_dir():
        raise HTTPException(404, "That voice isn't in the voices folder (Celine's original recording is removed by deleting the file).")
    _trash(folder)
    return {'removed': voice_id}
