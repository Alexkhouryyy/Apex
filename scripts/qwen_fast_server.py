"""Celine, streamed: the faster Qwen engine behind the same local API.

Measured on the RTX 4070 laptop with scripts/test_fast_qwen.py: the first
audio of a sentence in 0.94-0.99 s once warm, generated slightly faster than
it plays (real-time factor ~0.9). The original server (scripts/qwen_server.py)
made the WHOLE section before returning a byte — 19.8 s for 2.1 s of speech.

Runs in %USERPROFILE%\\apex-qwen-fast-env (Test-Apex-Fast-Voice.cmd installs
it). Same loopback-only rules and the same routes as qwen_server.py, plus one:

    GET  /health            {"model_loaded": true, "streaming": true} once warm
    GET  /profiles          [CELINE]
    POST /generate/stream   a complete WAV — what Apex's Voicebox adapter reads
    POST /generate/pcm      raw 16-bit mono PCM, sent as it is generated;
                            the sample rate is in the X-Sample-Rate header

The first generation on a fresh process captures CUDA graphs, which took 77 s
on the laptop. That happens here, at startup, before /health reports ready —
not on the user's first question.
"""

import argparse
import io
import json
import logging
import threading
from pathlib import Path

try:
    from qwen_server import PROFILE, TRANSCRIPT
except ImportError:                              # run from the repo root
    from scripts.qwen_server import PROFILE, TRANSCRIPT

WARMUP_TEXT = "Hi. I'm warming up, and I'll be ready in a moment."
MAX_CHARS = 4000


def pcm16(chunk) -> bytes:
    """float audio in [-1, 1] -> little-endian 16-bit PCM bytes."""
    import numpy as np
    a = np.clip(np.asarray(chunk, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (a * 32767.0).astype("<i2").tobytes()


class FastVoice:
    """The faster engine, warmed up, with Celine's reference prompt cached."""

    def __init__(self, reference: Path, transcript: str, chunk_size: int = 8):
        import torch
        import numpy as np
        import soundfile as sf
        from faster_qwen3_tts import FasterQwen3TTS
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable. Use the apex-qwen-fast-env Python.')
        audio, rate = sf.read(str(reference), dtype='float32')
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if not 0 < len(audio) / rate <= 120 or not np.isfinite(audio).all():
            raise ValueError('Reference audio must be valid and at most 120 seconds.')
        # The engine caches the voice prompt keyed on the reference PATH and
        # transcript, so one stable WAV path means the clone is built once.
        self.reference = Path.home() / 'apex-fast-voice-results' / 'celine-reference.wav'
        self.reference.parent.mkdir(exist_ok=True)
        sf.write(self.reference, audio, rate)
        self.transcript = transcript
        self.chunk_size = chunk_size
        self.model = FasterQwen3TTS.from_pretrained('Qwen/Qwen3-TTS-12Hz-1.7B-Base')
        self.sample_rate = int(getattr(self.model, 'sample_rate', 0) or 24000)

    def stream(self, text: str):
        """Yield float32 chunks as they are generated."""
        import torch
        with torch.inference_mode():
            for chunk, rate, _info in self.model.generate_voice_clone_streaming(
                    text=text, language='English', ref_audio=str(self.reference),
                    ref_text=self.transcript, chunk_size=self.chunk_size,
                    max_new_tokens=2048):
                if int(rate) != self.sample_rate:
                    raise RuntimeError(f'sample rate changed mid-stream: {rate} != {self.sample_rate}')
                yield chunk

    def warm_up(self) -> None:
        for _ in self.stream(WARMUP_TEXT):
            pass


def create_app(voice):
    """`voice` needs .sample_rate and .stream(text) -> iterable of float chunks."""
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.responses import StreamingResponse
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = threading.Lock()

    @app.middleware('http')
    async def local_only(request, call_next):
        # Apex calls server-to-server. A browser-originated request, or a
        # non-loopback Host, is a web page trying to drive the GPU.
        if request.headers.get('origin') or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            return Response(status_code=403)
        return await call_next(request)

    @app.get('/health')
    def health():
        return {'status': 'healthy', 'service': 'apex-qwen', 'model_loaded': True,
                'profile': 'celine', 'streaming': True, 'engine': 'faster-qwen3-tts',
                'sample_rate': voice.sample_rate}

    @app.get('/profiles')
    def profiles():
        return [PROFILE]

    async def read_text(request) -> str:
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
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_CHARS:
            raise HTTPException(400, f'Speech must contain 1-{MAX_CHARS} characters')
        if body.get('profile_id', 'celine') != 'celine' or body.get('engine', 'qwen') != 'qwen':
            raise HTTPException(400, 'Select CELINE in the Apex voice picker')
        return text.strip()

    def take_gpu():
        if not gate.acquire(blocking=False):
            raise HTTPException(409, 'Celine is generating another section; try again shortly')

    @app.post('/generate/stream')
    async def generate_wav(request: Request):
        """The whole section as one WAV — for callers that cannot stream."""
        text = await read_text(request)
        take_gpu()

        def run():
            import wave
            try:
                frames = b''.join(pcm16(c) for c in voice.stream(text))
            finally:
                gate.release()
            if not frames:
                raise RuntimeError('the model returned no audio')
            out = io.BytesIO()
            with wave.open(out, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(voice.sample_rate)
                w.writeframes(frames)
            return out.getvalue()

        import anyio
        try:
            data = await anyio.to_thread.run_sync(run, abandon_on_cancel=False)
        except Exception:
            logging.exception('Fast Qwen generation failed')
            raise HTTPException(503, 'Local Qwen generation failed; see the launcher console')
        return Response(data, media_type='audio/wav')

    @app.post('/generate/pcm')
    async def generate_pcm(request: Request):
        """Audio as it is made. The GPU lock is held until generation ends —
        even if the listener hangs up — because stopping the HTTP response
        does not stop the GPU, and a second generation would fight it."""
        text = await read_text(request)
        take_gpu()
        import anyio
        import queue as _queue
        q: _queue.Queue = _queue.Queue(maxsize=64)
        DONE, FAIL = object(), object()

        def produce():
            try:
                for chunk in voice.stream(text):
                    q.put(pcm16(chunk))
                q.put(DONE)
            except Exception:
                logging.exception('Fast Qwen streaming failed')
                q.put(FAIL)
            finally:
                gate.release()

        threading.Thread(target=produce, daemon=True, name='celine-stream').start()

        async def body():
            while True:
                item = await anyio.to_thread.run_sync(q.get)
                if item is DONE or item is FAIL:
                    # A failure mid-stream cannot change the status already
                    # sent; ending early is the signal, and the console says why.
                    return
                yield item

        return StreamingResponse(body(), media_type='application/octet-stream',
                                 headers={'X-Sample-Rate': str(voice.sample_rate),
                                          'X-Audio-Format': 'pcm_s16le',
                                          'Cache-Control': 'no-store'})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--reference', type=Path, default=Path.home() / 'Downloads' / 'celine.ogg')
    parser.add_argument('--transcript', type=Path, help='Optional UTF-8 transcript matching the reference')
    parser.add_argument('--port', type=int, default=17494)
    args = parser.parse_args()
    if not args.reference.is_file():
        parser.error(f'Recording missing: {args.reference}')
    transcript = args.transcript.read_text(encoding='utf-8-sig') if args.transcript else TRANSCRIPT
    print('Loading the fast Qwen engine and preparing Celine. Keep this console open.', flush=True)
    voice = FastVoice(args.reference, transcript)
    print('Warming up (one time per start, about 1-2 minutes on the first run)...', flush=True)
    voice.warm_up()
    print('CELINE READY (streaming): first audio in about a second from here on.', flush=True)
    import uvicorn
    uvicorn.run(create_app(voice), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
