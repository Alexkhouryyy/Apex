"""Owner setup diagnostics and bounded, read-only upstream update checks."""
from __future__ import annotations

import asyncio
import json
import re
import time

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from dashboard.apps import owner
from dashboard.world_engine import ENGINE, ROOT, runtime

router = APIRouter()
api = APIRouter(prefix='/api/setup', dependencies=[Depends(owner)])
_update_lock = asyncio.Lock()
_update_cache = None
_checked_at = 0.0
UPSTREAM = 'https://api.github.com/repos/bilawalsidhu/gods-eye-view/commits/main'


@router.get('/setup')
def shell():
    return FileResponse(ROOT / 'dashboard/static/setup.html', headers={'Cache-Control': 'no-store'})


@api.get('/status')
async def status():
    import config
    from agent import apps, provider
    from dashboard import server
    from voice.voicebox import base_url, resolve_engine
    model = server._agent_ref._model if server._agent_ref else config.AGENT_MODEL
    kind = provider.provider_for(model)
    key_name = {'anthropic': 'ANTHROPIC_API_KEY', 'openai': 'OPENAI_API_KEY',
                'gemini': 'GEMINI_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY'}.get(kind)
    result = {'engine': runtime.status(), 'model': {'name': model, 'provider': kind,
        'configured': bool(getattr(config, key_name, '')) if key_name else bool(config.OLLAMA_BASE_URL),
        'key_setting': key_name, 'verified': False},
        'apps': {'configured': bool(apps._key()), 'authorization_required': True},
        'voice': {'state': 'unavailable', 'detail': 'Start Apex with Start-Apex-All.cmd, then refresh.'},
        'updates': _update_cache or {'state': 'not_checked', 'automatic_install': False}}
    # Local health/profiles only. Never generate audio or call a paid model here.
    try:
        async with httpx.AsyncClient(base_url=base_url(), trust_env=False, timeout=3,
                                     follow_redirects=False) as client:
            health, profiles = await asyncio.gather(client.get('/health'), client.get('/profiles'))
        health.raise_for_status()
        profiles.raise_for_status()
        rows = profiles.json()
        if not isinstance(rows, list):
            raise ValueError('Unexpected voice profiles')
        selected = config.VOICEBOX_PROFILE or 'celine'
        matches = [p for p in rows if isinstance(p, dict) and
                   (p.get('id') == selected or str(p.get('name', '')).casefold() == selected.casefold())
                   and resolve_engine(p) is not None]
        result['voice'] = {'state': 'ready' if len(matches) == 1 else 'profile_missing',
            'detail': 'Celine server and profile answered. Use Test Celine to check playback.' if len(matches) == 1
                      else 'Voice server answered. Select your Celine profile in Settings & keys.',
            'audio_verified': False, 'profile_id': matches[0].get('id') if len(matches) == 1 else None}
    except (httpx.HTTPError, ValueError):
        pass
    return result


@api.post('/world-update')
async def check_update():
    """Contact the fixed upstream only; never pull or execute downloaded code."""
    global _update_cache, _checked_at
    async with _update_lock:
        if _update_cache and time.monotonic() - _checked_at < 900:
            return _update_cache
        installed = json.loads((ENGINE / 'APEX_UPSTREAM.json').read_text(encoding='utf-8'))['revision']
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10, follow_redirects=False) as client:
                response = await client.get(UPSTREAM, headers={'Accept': 'application/vnd.github+json',
                    'User-Agent': 'Apex-World-Update-Check'})
            response.raise_for_status()
            latest = response.json().get('sha')
            if not isinstance(latest, str) or not re.fullmatch(r'[0-9a-f]{40}', latest):
                raise ValueError('Invalid upstream revision')
        except (httpx.HTTPError, ValueError, AttributeError):
            return {'state': 'unavailable', 'installed': installed, 'automatic_install': False,
                'detail': 'Could not check GitHub. The installed World View is unchanged; retry later.'}
        _update_cache = {'state': 'current' if latest == installed else 'update_available',
            'installed': installed, 'latest': latest, 'automatic_install': False,
            'checked_at': int(time.time()),
            'detail': 'World View matches the current God’s Eye release.' if latest == installed else
                      'A newer God’s Eye revision is available. It needs an Apex integration review before installation.'}
        _checked_at = time.monotonic()
        return _update_cache


router.include_router(api)
