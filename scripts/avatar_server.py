"""Apex's photoreal video avatar: a real face that lip-syncs Apex's voice.

    python scripts/avatar_server.py --video C:\\path\\apex-idle.mp4                 # MuseTalk on the GPU
    python scripts/avatar_server.py --video C:\\path\\apex-idle.mp4 --engine still  # no GPU: plays the loop, no lip-sync

Runs on the PC beside the voice server (Setup-Apex-Video-Avatar.cmd installs it),
loopback only, like the voice servers; Apex reaches it at AVATAR_URL and the
companion never talks to it directly.

    GET  /health    {"ready": true, "engine": "musetalk", "fps": 25, "frames": N}
    GET  /idle      the character's idle loop (no sound)
    POST /lipsync   body: one spoken section as WAV  ->  a clip of the face saying it,
                    with that audio. ?start=N begins on frame N of the idle loop
                    (the one on screen), and X-End-Frame says where it ended, so
                    talking starts and stops without a jump. X-Render-Ms (and
                    Server-Timing) say how long it took
    Both take ?format=mp4 (default; Chrome, Edge, Safari) or ?format=webm.

The face is a video of the character (`apex-idle.mp4`): looking at the camera,
blinking, moving a little, mouth closed, 5 to 10 seconds, 25 fps. MuseTalk
(TMElyralab/MuseTalk, MIT, pinned in Setup-Apex-Video-Avatar.cmd) redraws only
the mouth area of those real frames to match the audio, which is why the result
stays photoreal. Its slow part (face detection, masks, latents) runs once per
video and is cached; each sentence then runs only the fast part.
"""

import argparse
import hashlib
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = Path.home() / 'apex-video-avatar'
MAX_AUDIO_BYTES = 8_000_000                 # about 90 s of 24 kHz speech: far more than one section
FPS = 25                                    # what MuseTalk was trained on


# ---------------------------------------------------------------- video in and out (PyAV)

def read_frames(path, limit=750):
    """BGR frames of a video (up to `limit`), and its frame rate."""
    import av
    with av.open(path if hasattr(path, 'read') else str(path)) as box:
        stream = box.streams.video[0]
        rate = float(stream.average_rate or FPS)
        frames = []
        for frame in box.decode(stream):
            frames.append(frame.to_ndarray(format='bgr24'))
            if len(frames) >= limit:
                break
    if not frames:
        raise ValueError(f'{path} has no video frames.')
    return frames, rate


def decode_audio(wav_bytes):
    """Mono float32 samples and their rate, from any audio PyAV can read."""
    import av
    import numpy as np
    with av.open(io.BytesIO(wav_bytes)) as box:
        stream = box.streams.audio[0]
        resampler = av.AudioResampler(format='flt', layout='mono', rate=stream.rate or 24000)
        parts = [r.to_ndarray().reshape(-1) for f in box.decode(stream) for r in resampler.resample(f)]
        rate = stream.rate or 24000
    samples = np.concatenate(parts) if parts else np.zeros(0, dtype='float32')
    return samples.astype('float32'), rate


FORMATS = {'mp4': 'video/mp4', 'webm': 'video/webm'}


def encode_video(frames, fps, audio=None, fmt='mp4'):
    """A clip the browser can play: MP4 (H.264 + AAC) for Chrome, Edge and
    Safari, or WebM (VP8 + Opus) for browsers without H.264 (open-source
    Chromium). Written to memory, so MP4 is fragmented."""
    import av
    import numpy as np
    out = io.BytesIO()
    options = {'movflags': 'frag_keyframe+empty_moov+default_base_moof'} if fmt == 'mp4' else {}
    with av.open(out, 'w', format=fmt, options=options) as box:
        h, w = frames[0].shape[:2]
        video = box.add_stream('libx264' if fmt == 'mp4' else 'libvpx', rate=int(round(fps)))
        video.width, video.height = w - w % 2, h - h % 2
        video.pix_fmt = 'yuv420p'
        video.options = ({'preset': 'veryfast', 'crf': '20'} if fmt == 'mp4'
                         else {'deadline': 'realtime', 'cpu-used': '8', 'crf': '10', 'b': '2M'})
        sound = resampler = None
        if audio is not None:
            samples, rate = audio
            out_rate = rate if fmt == 'mp4' else 48000           # Opus only takes 48 kHz (and a few others)
            sound = box.add_stream('aac' if fmt == 'mp4' else 'libopus', rate=out_rate)
            sound.layout = 'mono'
            resampler = av.AudioResampler(format=sound.codec_context.format.name, layout='mono', rate=out_rate)
        for bgr in frames:
            frame = av.VideoFrame.from_ndarray(np.ascontiguousarray(bgr[:video.height, :video.width]), format='bgr24')
            for packet in video.encode(frame):
                box.mux(packet)
        for packet in video.encode():
            box.mux(packet)
        if sound is not None:
            step = 960
            for i in range(0, len(samples), step):
                chunk = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[i:i + step].reshape(1, -1)), format='flt', layout='mono')
                chunk.sample_rate = rate
                for converted in resampler.resample(chunk):
                    for packet in sound.encode(converted):
                        box.mux(packet)
            for converted in resampler.resample(None):
                for packet in sound.encode(converted):
                    box.mux(packet)
            for packet in sound.encode():
                box.mux(packet)
    return out.getvalue()


def make_idle(source, target, seconds=10, max_height=720):
    """Turn any video of the character into the idle loop MuseTalk wants:
    25 fps (the rate it was trained at), at most `seconds` long, at most
    `max_height` tall. Frames are picked by time, so any source rate works."""
    import av
    import numpy as np
    picked, next_t = [], 0.0
    with av.open(str(source)) as box:
        stream = box.streams.video[0]
        for frame in box.decode(stream):
            t = float(frame.time or 0)
            if t > seconds:
                break
            image = frame.to_ndarray(format='bgr24')
            while t + 1e-6 >= next_t and next_t <= seconds:
                picked.append(image); next_t += 1 / FPS
    if not picked:
        raise ValueError(f'{source} has no video frames.')
    h, w = picked[0].shape[:2]
    if h > max_height:
        import cv2
        scale = max_height / h
        picked = [cv2.resize(f, (int(w * scale) // 2 * 2, max_height), interpolation=cv2.INTER_AREA) for f in picked]
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_bytes(encode_video(picked, FPS))
    return len(picked)


# ---------------------------------------------------------------- engines

class StillEngine:
    """No GPU: the idle loop with the voice laid over it. The mouth doesn't
    move; it keeps the whole path working (and testable) without MuseTalk."""
    name = 'still'

    def __init__(self, video):
        frames, self.fps = read_frames(video)
        # Forward then backward, like MuseTalk's prepared cycle: the loop never jumps.
        self.frames = frames + frames[-2:0:-1] if len(frames) > 2 else frames
        self.idle_frames = self.frames

    def render(self, wav_bytes, fmt='mp4', start=0):
        samples, rate = decode_audio(wav_bytes)
        count = max(1, int(round(len(samples) / rate * self.fps)))
        frames = [self.frames[(start + i) % len(self.frames)] for i in range(count)]
        return encode_video(frames, self.fps, (samples, rate), fmt), (start + count) % len(self.frames)


class MuseTalkEngine:
    """MuseTalk 1.5, following its own scripts/realtime_inference.py: prepare
    the face once (cached per video), then per section only audio features,
    the UNet and the VAE decoder run."""
    name = 'musetalk'

    def __init__(self, video, musetalk_dir, batch_size=16):
        import pickle
        import numpy as np
        root = Path(musetalk_dir).resolve()
        if not (root / 'musetalk').is_dir():
            raise SystemExit(f'MuseTalk is not at {root}. Run Setup-Apex-Video-Avatar.cmd.')
        os.chdir(root)                                 # its modules load ./models/... at import time
        sys.path.insert(0, str(root))
        import torch
        from transformers import WhisperModel
        from musetalk.utils.utils import load_all_model, datagen
        from musetalk.utils.preprocessing import get_landmark_and_bbox
        from musetalk.utils.blending import get_image_prepare_material, get_image_blending
        from musetalk.utils.audio_processor import AudioProcessor
        from musetalk.utils.face_parsing import FaceParsing
        import cv2
        self.torch, self.np, self.cv2, self.datagen, self.blend = torch, np, cv2, datagen, get_image_blending
        self.batch_size = batch_size
        self.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        if self.device.type != 'cuda':
            print('Warning: no CUDA GPU found. MuseTalk will be far too slow on the CPU.', flush=True)
        vae, unet, pe = load_all_model(unet_model_path=os.path.join('models', 'musetalkV15', 'unet.pth'),
                                       vae_type='sd-vae', unet_config=os.path.join('models', 'musetalkV15', 'musetalk.json'),
                                       device=self.device)
        self.timesteps = torch.tensor([0], device=self.device)
        self.pe = pe.half().to(self.device)
        vae.vae = vae.vae.half().to(self.device)
        unet.model = unet.model.half().to(self.device)
        self.vae, self.unet = vae, unet
        self.audio = AudioProcessor(feature_extractor_path='./models/whisper')
        self.dtype = unet.model.dtype
        self.whisper = WhisperModel.from_pretrained('./models/whisper').to(device=self.device, dtype=self.dtype).eval()
        self.whisper.requires_grad_(False)

        frames, self.fps = read_frames(video)
        if abs(self.fps - FPS) > 0.5:
            print(f'Note: the video is {self.fps:.0f} fps; MuseTalk is trained at {FPS}. Re-encode it to {FPS} fps for the best result.', flush=True)
        digest = hashlib.sha256(Path(video).read_bytes()).hexdigest()[:16]
        cache = HOME / 'cache' / digest
        if (cache / 'prepared.pkl').exists():
            print('Using the prepared face from the cache.', flush=True)
            with open(cache / 'prepared.pkl', 'rb') as f:
                data = pickle.load(f)
            latents = torch.load(cache / 'latents.pt')
        else:
            print(f'Preparing the face once ({len(frames)} frames; a few minutes)...', flush=True)
            cache.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp())
            paths = []
            for i, f in enumerate(frames):
                p = tmp / f'{i:08d}.png'; cv2.imwrite(str(p), f); paths.append(str(p))
            coords, read = get_landmark_and_bbox(paths, 0)
            fp = FaceParsing(left_cheek_width=90, right_cheek_width=90)
            latents, kept_coords, kept_frames = [], [], []
            for box, frame in zip(coords, read):
                if box == (0.0, 0.0, 0.0, 0.0):
                    continue                           # no face found in this frame: leave it out
                x1, y1, x2, y2 = box
                y2 = min(y2 + 10, frame.shape[0])      # extra_margin, as MuseTalk 1.5 does
                crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
                latents.append(vae.get_latents_for_unet(crop))
                kept_coords.append([x1, y1, x2, y2]); kept_frames.append(frame)
            if not latents:
                raise SystemExit('No face was found in the video. Use a clear, front-facing shot of the character.')
            # Forward then backward, so the loop never jumps.
            cycle = kept_frames + kept_frames[::-1]
            coords_cycle = kept_coords + kept_coords[::-1]
            latents = latents + latents[::-1]
            masks, boxes = [], []
            for frame, box in zip(cycle, coords_cycle):
                mask, crop_box = get_image_prepare_material(frame, box, fp=fp, mode='jaw')
                masks.append(mask); boxes.append(crop_box)
            data = dict(frames=cycle, coords=coords_cycle, masks=masks, boxes=boxes)
            with open(cache / 'prepared.pkl', 'wb') as f:
                pickle.dump(data, f)
            torch.save(latents, cache / 'latents.pt')
        self.frames, self.coords, self.masks, self.boxes, self.latents = (
            data['frames'], data['coords'], data['masks'], data['boxes'], latents)
        self.idle_frames = data['frames']

    def render(self, wav_bytes, fmt='mp4', start=0):
        torch, np, cv2 = self.torch, self.np, self.cv2
        samples, rate = decode_audio(wav_bytes)
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp.write(wav_bytes)
        try:
            with torch.no_grad():
                features, length = self.audio.get_audio_feature(tmp.name, weight_dtype=self.dtype)
                chunks = self.audio.get_whisper_chunk(features, self.device, self.dtype, self.whisper, length,
                                                      fps=self.fps, audio_padding_length_left=2, audio_padding_length_right=2)
                out, idx = [], 0
                for whisper_batch, latent_batch in self.datagen(chunks, self.latents, self.batch_size, delay_frame=start, device=self.device):
                    audio_batch = self.pe(whisper_batch.to(self.device))
                    latent_batch = latent_batch.to(device=self.device, dtype=self.unet.model.dtype)
                    pred = self.unet.model(latent_batch, self.timesteps, encoder_hidden_states=audio_batch).sample
                    for face in self.vae.decode_latents(pred.to(device=self.device, dtype=self.vae.vae.dtype)):
                        n = (start + idx) % len(self.frames)
                        x1, y1, x2, y2 = self.coords[n]
                        face = cv2.resize(face.astype(np.uint8), (x2 - x1, y2 - y1))
                        out.append(self.blend(self.frames[n].copy(), face, [x1, y1, x2, y2], self.masks[n], self.boxes[n]))
                        idx += 1
        finally:
            os.unlink(tmp.name)
        return encode_video(out, self.fps, (samples, rate), fmt), (start + len(out)) % len(self.frames)


# ---------------------------------------------------------------- the server

def create_app(engine):
    from fastapi import FastAPI, HTTPException, Request, Response
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = threading.Lock()                  # one GPU job at a time, in the order asked
    idle = {}

    @app.middleware('http')
    async def local_only(request, call_next):
        # Apex calls server-to-server. A browser-originated request, or a
        # non-loopback Host, is a web page trying to drive the GPU.
        if request.headers.get('origin') or request.url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            return Response(status_code=403)
        return await call_next(request)

    @app.get('/health')
    def health():
        return {'ready': True, 'service': 'apex-avatar', 'engine': engine.name, 'fps': engine.fps,
                'frames': len(engine.idle_frames)}

    def kind(request):
        fmt = request.query_params.get('format', 'mp4')
        if fmt not in FORMATS:
            raise HTTPException(400, 'format must be mp4 or webm.')
        return fmt

    @app.get('/idle')
    def idle_loop(request: Request):
        fmt = kind(request)
        if fmt not in idle:
            frames = engine.idle_frames
            idle[fmt] = encode_video(frames, engine.fps, fmt=fmt)
        return Response(idle[fmt], media_type=FORMATS[fmt], headers={'Cache-Control': 'no-store'})

    @app.post('/lipsync')
    async def lipsync(request: Request):
        fmt = kind(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_AUDIO_BYTES:
                raise HTTPException(413, 'Audio too long for one section.')
        if not raw:
            raise HTTPException(400, 'Send the section as WAV audio.')
        try:
            start = int(request.query_params.get('start', 0))
        except ValueError:
            raise HTTPException(400, 'start must be a frame number.')
        started = time.perf_counter()
        try:
            with gate:
                video, end = engine.render(bytes(raw), fmt, start % len(engine.idle_frames))
        except ValueError as exc:
            raise HTTPException(400, f'Could not read that audio: {exc}') from exc
        took = int((time.perf_counter() - started) * 1000)
        # X-End-Frame: where the clip stopped in the loop, so the idle loop picks up from there.
        return Response(video, media_type=FORMATS[fmt], headers={'X-Render-Ms': str(took), 'X-End-Frame': str(end),
                        'Server-Timing': f'avatar;dur={took}', 'Cache-Control': 'no-store'})

    return app


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--video', type=Path, default=HOME / 'apex-idle.mp4', help='the character idle loop (see the top of this file)')
    p.add_argument('--engine', choices=('musetalk', 'still'), default='musetalk')
    p.add_argument('--musetalk', type=Path, default=HOME / 'MuseTalk')
    p.add_argument('--port', type=int, default=17495)
    p.add_argument('--make-idle', type=Path, metavar='SOURCE', help='turn any video of the character into the idle loop at --video, then exit')
    a = p.parse_args(argv)
    if a.make_idle:
        count = make_idle(a.make_idle, a.video)
        print(f'Wrote {a.video}: {count} frames at {FPS} fps ({count / FPS:.1f} s).')
        return
    if not a.video.is_file():
        p.error(f'No idle video at {a.video}. See docs/VIDEO_AVATAR.md for how to make one.')
    print(f'Loading the {a.engine} engine. Keep this console open.', flush=True)
    engine = MuseTalkEngine(a.video, a.musetalk) if a.engine == 'musetalk' else StillEngine(a.video)
    print(f'AVATAR READY ({engine.name}) on http://127.0.0.1:{a.port}', flush=True)
    import uvicorn
    uvicorn.run(create_app(engine), host='127.0.0.1', port=a.port)


if __name__ == '__main__':
    main()
