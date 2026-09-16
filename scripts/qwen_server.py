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


class QwenVoice:
    def __init__(self, reference, transcript):
        import torch
        import soundfile as sf
        from qwen_tts import Qwen3TTSModel
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA unavailable. Use the GPU-enabled apex-qwen-env Python.')
        audio, rate = sf.read(str(reference), dtype='float32')
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if not 0 < len(audio) / rate <= 120:
            raise ValueError('Reference audio must be nonempty and at most 120 seconds.')
        self.model = Qwen3TTSModel.from_pretrained(
            'Qwen/Qwen3-TTS-12Hz-1.7B-Base', device_map='cuda:0',
            dtype=torch.bfloat16, attn_implementation='sdpa')
        with torch.inference_mode():
            self.prompt = self.model.create_voice_clone_prompt(
                ref_audio=(audio, rate), ref_text=transcript, x_vector_only_mode=False)

    def generate(self, text):
        import torch
        import soundfile as sf
        with torch.inference_mode():
            wavs, rate = self.model.generate_voice_clone(
                text=text, language='English', voice_clone_prompt=self.prompt,
                max_new_tokens=4096)
        output = io.BytesIO()
        sf.write(output, wavs[0], rate, format='WAV', subtype='PCM_16')
        return output.getvalue()


def create_app(voice):
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
        return {'status': 'healthy', 'service': 'apex-qwen', 'model_loaded': True, 'profile': 'celine'}

    @app.get('/profiles')
    def profiles():
        return [PROFILE]

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
        if body.get('profile_id', 'celine') != 'celine' or body.get('engine', 'qwen') != 'qwen':
            raise HTTPException(400, 'Select CELINE in the Apex voice picker')
        if not gate.acquire(blocking=False):
            raise HTTPException(409, 'Celine is generating another reply; try again shortly')
        def run():
            try:
                return voice.generate(text.strip())
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
    parser.add_argument('--reference', type=Path, default=Path.home() / 'Downloads' / 'celine.ogg')
    parser.add_argument('--transcript', type=Path, help='Optional UTF-8 transcript matching the reference')
    parser.add_argument('--port', type=int, default=17494)
    args = parser.parse_args()
    if not args.reference.is_file():
        parser.error(f'Recording missing: {args.reference}')
    transcript = args.transcript.read_text(encoding='utf-8-sig') if args.transcript else TRANSCRIPT
    print('Loading Qwen and preparing Celine once. Keep this console open.', flush=True)
    voice = QwenVoice(args.reference, transcript)
    print('CELINE READY: model and reference prompt will stay in memory.', flush=True)
    import uvicorn
    uvicorn.run(create_app(voice), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
