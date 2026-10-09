"""Persistent local Celine TTS; implements the subset of Voicebox Apex uses."""
import argparse
import io
import json
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

TRANSCRIPT = """Hey Alex, I'm here. What are we working on today? Let's take a moment to understand the problem before we jump into a solution. Your idea makes sense, although there's one detail I'd check first. Did that change actually fix the issue, or did it just make the error disappear? Give me a second to look through the results. Okay, that's promising. We've made progress, and the next step is clear."""
PROFILE = dict(id='celine', name='CELINE', voice_type='cloned', language='en', default_engine='qwen')

try:
    import voice_library
except ImportError:                              # imported from the repo root
    from scripts import voice_library


PAGE_FILE_HELP = (
    "Windows ran out of virtual memory (RAM plus the page file) loading Celine's voice "
    "(Windows error 1455: the paging file is too small). Fix it once: press Win+R, type "
    "sysdm.cpl, then Advanced > Performance Settings > Advanced > Virtual memory > Change. "
    "Tick 'Automatically manage paging file size for all drives' (or set C: to a custom size "
    "of 16384 to 32768 MB), press Set and OK, and restart the PC. Closing big programs "
    "(games, many browser tabs) helps until then.")


def _page_file(exc: OSError) -> None:
    """Windows error 1455 in plain words, instead of a traceback."""
    if getattr(exc, 'winerror', None) == 1455 or '1455' in str(exc) or 'paging file' in str(exc).lower():
        raise RuntimeError(PAGE_FILE_HELP) from exc


def load_model(load):
    """Load the voice model with `load()`, surviving one Windows quirk.

    transformers first reserves the whole model as ONE block of GPU memory
    (caching_allocator_warmup: about 3.6 GB for this model) so loading is
    faster. On Windows that single block can be refused even with plenty
    free ("Tried to allocate 3.59 GiB ... 6.89 GiB is free"), because other
    apps split the GPU's memory. Then this loads again without the
    warm-up, tensor by tensor: a little slower to load, the same model. A
    second refusal is a real shortage, and the message says what to do."""
    import torch
    try:
        return load()
    except torch.OutOfMemoryError:
        pass
    except OSError as exc:
        _page_file(exc)
        raise
    print('The GPU refused one big block of memory; loading again piece by piece...', flush=True)
    torch.cuda.empty_cache()
    from transformers import modeling_utils
    was = getattr(modeling_utils, 'caching_allocator_warmup', None)
    modeling_utils.caching_allocator_warmup = lambda *args, **kwargs: None
    try:
        return load()
    except OSError as exc:
        _page_file(exc)
        raise
    except torch.OutOfMemoryError as exc:
        raise RuntimeError('Not enough GPU memory for Celine\'s voice (about 4 GB). Close other programs '
                           'using the GPU (nvidia-smi lists them: games, other Apex windows, video '
                           'apps), then start again.') from exc
    finally:
        if was is not None:
            modeling_utils.caching_allocator_warmup = was


class QwenVoice:
    """The model, loaded once; any voice in the library. Each voice's clone
    prompt is built on its first use and kept until its recording changes."""

    def __init__(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable. Use the GPU-enabled apex-qwen-env Python.')
        self.model = load_model(lambda: Qwen3TTSModel.from_pretrained(
            'Qwen/Qwen3-TTS-12Hz-1.7B-Base', device_map='cuda:0',
            dtype=torch.bfloat16, attn_implementation='sdpa'))
        self.prompts: dict[str, tuple[float, object]] = {}

    def prompt(self, voice):
        import torch
        import soundfile as sf
        known = self.prompts.get(voice.id)
        if known and known[0] == voice.stamp:
            return known[1]
        audio, rate = sf.read(str(voice.reference), dtype='float32')
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if not 0 < len(audio) / rate <= 120:
            raise ValueError(f'The {voice.name} recording must be nonempty and at most 120 seconds.')
        with torch.inference_mode():
            prompt = self.model.create_voice_clone_prompt(
                ref_audio=(audio, rate), ref_text=voice.transcript, x_vector_only_mode=False)
        self.prompts[voice.id] = (voice.stamp, prompt)
        return prompt

    def generate(self, text, voice):
        import torch
        import soundfile as sf
        prompt = self.prompt(voice)
        with torch.inference_mode():
            wavs, rate = self.model.generate_voice_clone(
                text=text, language='English', voice_clone_prompt=prompt,
                max_new_tokens=4096)
        output = io.BytesIO()
        sf.write(output, wavs[0], rate, format='WAV', subtype='PCM_16')
        return output.getvalue()


def create_app(voice, voices=None):
    voices = voices or (lambda: voice_library.discover())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = threading.Lock()

    @app.middleware('http')
    async def local_only(request, call_next):
        # Apex calls server-to-server. Reject browser-originated requests and
        # non-loopback Hosts to prevent a web page driving this local service.
        if request.headers.get('origin') or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            return Response(status_code=403)
        return await call_next(request)

    @app.get('/health')
    def health():
        found = voices()
        return {'status': 'healthy', 'service': 'apex-qwen', 'model_loaded': True,
                'profile': found[0].id if found else None, 'voices': [v.id for v in found]}

    @app.get('/profiles')
    def profiles():
        return [v.profile() for v in voices()]

    @app.post('/generate/stream')
    async def generate(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 24000:
                raise HTTPException(413, 'Speech request too large')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'Invalid JSON')
        if not isinstance(body, dict):
            raise HTTPException(400, 'Expected a JSON object')
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise HTTPException(400, 'Speech must contain 1-4000 characters')
        wanted = body.get('profile_id', '')
        if not isinstance(wanted, str) or len(wanted) > 200 or body.get('engine', 'qwen') != 'qwen':
            raise HTTPException(400, 'Choose a voice in the Apex voice picker')
        try:
            chosen = voice_library.choose(voices(), wanted)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not gate.acquire(blocking=False):
            raise HTTPException(409, 'The voice is generating another reply; try again shortly')
        def run():
            try:
                return voice.generate(text.strip(), chosen)
            finally:
                gate.release()
        # Shield the worker so disconnects cannot release the GPU lock early.
        import anyio
        try:
            data = await anyio.to_thread.run_sync(run, abandon_on_cancel=False)
        except Exception:
            logging.exception('Qwen generation failed')
            raise HTTPException(503, 'Local Qwen generation failed; see the launcher console')
        return Response(data, media_type='audio/wav')

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=Path.home() / 'Downloads' / 'celine.ogg',
                        help="Celine's original recording; used when there is no celine folder")
    parser.add_argument('--transcript', type=Path, help='Optional UTF-8 transcript matching the reference')
    parser.add_argument('--port', type=int, default=17494)
    args = parser.parse_args()
    transcript = args.transcript.read_text(encoding='utf-8-sig') if args.transcript else TRANSCRIPT
    library = lambda: voice_library.discover(legacy=args.reference, legacy_transcript=transcript)
    found = library()
    if not found:
        parser.error(f'No voices: {args.reference} is missing and {voice_library.voices_dir()} has none. '
                     'Record one on the Voices page in Apex.')
    print('Loading Qwen once. Keep this console open.', flush=True)
    print('Voices: ' + ', '.join(v.name for v in found), flush=True)
    voice = QwenVoice()
    voice.prompt(found[0])
    print(f'{found[0].name} READY: model and reference prompt will stay in memory.', flush=True)
    import uvicorn
    uvicorn.run(create_app(voice, library), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
