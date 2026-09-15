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


QWEN_ENGINES = frozenset({'qwen', 'qwen_custom_voice'})

# Voice types Apex knows how to drive. Voicebox owns this vocabulary, not us,
# and its source is not readable from here — so the unknown case is handled
# below rather than guessed at.
CLONED_TYPES = frozenset({'cloned', 'clone'})
UNDRIVABLE_TYPES = frozenset({'import'})


def resolve_engine(profile):
    """The engine to ask Voicebox for, or None if Apex cannot drive this voice.

    ONE predicate, three callers: the picker (`profiles`), the dashboard route,
    and `synthesize`. They used to answer this question in four different ways —
    `profiles` dropped `import`, the route dropped presets whose engine was not
    qwen_custom_voice, `synthesize` re-fetched the raw list and applied neither,
    and its engine line mapped EVERY non-preset type to 'qwen'. So a profile the
    picker refused to show would still synthesize if its id was sent directly,
    and the list shown was not the list that worked.

    On the unknown case. A type this function has never heard of is still driven
    as a clone, which is exactly what the old code did — deliberately, and not
    because unknown-means-allowed is a good default. The vocabulary belongs to a
    third-party desktop app whose source is not on this machine; hard-failing an
    unrecognised type would break a working voice the first time Voicebox ships
    a new kind, and there is no safety stake here to justify that risk. The
    difference from before is that this is now ONE decision in ONE place, stated
    out loud, and changeable in a single line once somebody can read a live
    /profiles and settle what the values actually are.
    """
    if not isinstance(profile, dict):
        return None
    kind = (profile.get('voice_type') or '').strip().lower()
    if kind in UNDRIVABLE_TYPES:
        return None
    if kind == 'preset':
        engine = profile.get('preset_engine')
        return engine if engine in QWEN_ENGINES else None
    return 'qwen'


async def profiles():
    """Every profile Apex can actually speak with — the picker's list.

    Filtered by `resolve_engine`, so "shown in the dropdown" and "works when
    chosen" are the same set by construction rather than by two rules that
    happen to agree.
    """
    async with client() as c:
        rows = (await checked(c, 'GET', '/profiles')).json()
    if not isinstance(rows, list):
        raise ValueError('Unexpected Voicebox profile response. Update Voicebox.')
    return [p for p in rows if resolve_engine(p) is not None]


async def synthesize(text, profile_id=''):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('Speech must contain 1–4000 characters.')
    if not _gate.acquire(blocking=False):
        raise ValueError('Voicebox is already generating an Apex reply. Try again when it finishes.')
    try:
        async with asyncio.timeout(310):
            async with client() as c:
                rows = (await checked(c, 'GET', '/profiles')).json()
                if not isinstance(rows, list):
                    raise ValueError('Unexpected Voicebox profile response. Update Voicebox.')
                usable = [p for p in rows if resolve_engine(p) is not None]
                selected = profile_id or config.VOICEBOX_PROFILE
                if selected:
                    def _named(p):
                        return p.get('id') == selected or str(
                            p.get('name') or '').casefold() == selected.casefold()
                    matches = [p for p in usable if _named(p)]
                    if len(matches) != 1:
                        # Three different problems used to share one sentence,
                        # and "not found" is actively wrong for a profile that
                        # exists and simply cannot be driven — the user goes
                        # looking for a deleted voice that is sitting in front
                        # of them. A stale id in a browser's localStorage, or a
                        # VOICEBOX_PROFILE pinned in .env, reaches exactly here.
                        if len(matches) > 1:
                            raise ValueError(
                                f'The Voicebox profile name {selected!r} is ambiguous — '
                                f'{len(matches)} profiles share it. Select it by id in '
                                f'Apex, or rename one in Voicebox.')
                        blocked = [p for p in rows if _named(p)]
                        if blocked:
                            kind = blocked[0].get('voice_type') or 'unknown'
                            raise ValueError(
                                f'The Voicebox profile {selected!r} is a '
                                f'{kind} voice, which Apex cannot speak with. '
                                f'Pick a Qwen preset or a cloned voice in Apex.')
                        raise ValueError(
                            f'No Voicebox profile called {selected!r}. Select one in Apex.')
                    profile = matches[0]
                else:
                    # Reuse/create a dedicated preset; never alter existing cloned voices.
                    # Searched over the USABLE rows: an 'Apex Qwen Local' that
                    # cannot be driven must not be reused just because the name
                    # matches.
                    matches = [p for p in usable if p.get('name') == 'Apex Qwen Local' and p.get('preset_engine') == 'qwen_custom_voice' and p.get('preset_voice_id') == config.VOICEBOX_SPEAKER]
                    profile = matches[0] if matches else (await checked(c, 'POST', '/profiles', json={
                        'name': 'Apex Qwen Local', 'language': 'en', 'voice_type': 'preset',
                        'preset_engine': 'qwen_custom_voice', 'preset_voice_id': config.VOICEBOX_SPEAKER,
                        'default_engine': 'qwen_custom_voice',
                    })).json()
                engine = resolve_engine(profile)
                if engine is None:
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
