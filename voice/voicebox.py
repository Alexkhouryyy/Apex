"""Local Voicebox bridge. No cloud fallback or model downloads in Apex."""
import asyncio
import threading
from urllib.parse import urlsplit

import httpx
import config

_gate = threading.Lock()


def base_url():
    value = config.VOICEBOX_URL.rstrip('/')
    url = urlsplit(value)
    if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password or url.query or url.fragment or url.path:
        raise ValueError('VOICEBOX_URL must be a local HTTP address, e.g. http://127.0.0.1:17493')
    return value


def client():
    return httpx.AsyncClient(base_url=base_url(), trust_env=False,
                             timeout=httpx.Timeout(300, connect=5), follow_redirects=False)


async def checked(client, method, path, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code >= 300:
        try:
            detail = response.json().get('detail', 'Request failed')
        except Exception:
            detail = 'Request failed'
        raise ValueError(f'Voicebox {response.status_code}: {str(detail)[:500]}')
    return response


async def profiles():
    async with client() as c:
        rows = (await checked(c, 'GET', '/profiles')).json()
    if not isinstance(rows, list):
        raise ValueError('Unexpected Voicebox profile response. Update Voicebox.')
    return [p for p in rows if p.get('voice_type') != 'import']


async def synthesize(text, profile_id=''):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('Speech must contain 1–4000 characters.')
    if not _gate.acquire(blocking=False):
        raise ValueError('Voicebox is already generating an Apex reply. Try again when it finishes.')
    try:
        async with asyncio.timeout(310):
            async with client() as c:
                rows = (await checked(c, 'GET', '/profiles')).json()
                selected = profile_id or config.VOICEBOX_PROFILE
                if selected:
                    matches = [p for p in rows if p['id'] == selected or p['name'].casefold() == selected.casefold()]
                    if len(matches) != 1:
                        raise ValueError('Voicebox profile not found or name is ambiguous. Select a profile in Apex.')
                    profile = matches[0]
                else:
                    # Reuse/create a dedicated preset; never alter existing cloned voices.
                    matches = [p for p in rows if p.get('name') == 'Apex Qwen Local' and p.get('preset_engine') == 'qwen_custom_voice' and p.get('preset_voice_id') == config.VOICEBOX_SPEAKER]
                    profile = matches[0] if matches else (await checked(c, 'POST', '/profiles', json={
                        'name': 'Apex Qwen Local', 'language': 'en', 'voice_type': 'preset',
                        'preset_engine': 'qwen_custom_voice', 'preset_voice_id': config.VOICEBOX_SPEAKER,
                        'default_engine': 'qwen_custom_voice',
                    })).json()
                engine = profile.get('preset_engine') if profile.get('voice_type') == 'preset' else 'qwen'
                if engine not in {'qwen', 'qwen_custom_voice'}:
                    raise ValueError('Select a Qwen preset or a cloned voice profile for local Qwen speech.')
                async with c.stream('POST', '/generate/stream', json={
                    'text': text.strip(), 'profile_id': profile['id'], 'engine': engine,
                    'model_size': '1.7B', 'language': profile.get('language') or 'en',
                    'personality': False,
                }) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise ValueError(f'Voicebox could not generate audio ({response.status_code}): {body.decode(errors="replace")[:500]}')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 32 * 1024 * 1024:
                            raise ValueError('Voicebox audio exceeded the 32 MB limit.')
                    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
                        raise ValueError('Voicebox returned invalid WAV audio.')
                    return bytes(data)
    except (httpx.RequestError, TimeoutError) as exc:
        raise ValueError('Voicebox is unavailable or timed out. Keep Voicebox open on the Apex laptop and check its model status.') from exc
    finally:
        _gate.release()
