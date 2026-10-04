"""The photoreal video avatar (scripts/avatar_server.py, the /api/avatar routes).

The MuseTalk engine needs a GPU and its models, so it runs on the owner's PC;
everything around it runs here for real: video encoding in both browser
formats, the server's rules, and Apex's pass-through routes against a real
avatar server (the still engine) on a real port.
"""
import io
import socket
import threading
import time

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import avatar_server as av_server


def wav(seconds=1.5, rate=24000):
    import wave
    t = np.arange(int(seconds * rate)) / rate
    pcm = (8000 * np.sin(2 * np.pi * 180 * t)).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())
    return buf.getvalue()


@pytest.fixture(scope='module')
def idle(tmp_path_factory):
    path = tmp_path_factory.mktemp('avatar') / 'apex-idle.mp4'
    frames = [np.full((128, 96, 3), (i * 9) % 255, np.uint8) for i in range(25)]
    path.write_bytes(av_server.encode_video(frames, 25))
    return path


def streams(data):
    import av
    with av.open(io.BytesIO(data)) as box:
        return {s.type: s.codec_context.name for s in box.streams}, box.duration / 1e6


@pytest.mark.parametrize('fmt,video,audio', [('mp4', 'h264', 'aac'), ('webm', 'vp8', 'opus')])
def test_clips_play_in_browsers_and_last_as_long_as_the_voice(idle, fmt, video, audio):
    clip = av_server.StillEngine(idle).render(wav(1.5), fmt)
    kinds, seconds = streams(clip)
    assert kinds == {'video': video, 'audio': audio}
    assert abs(seconds - 1.5) < 0.15


@pytest.fixture
def client(idle):
    return TestClient(av_server.create_app(av_server.StillEngine(idle)), base_url='http://127.0.0.1')


def test_server_routes(client, monkeypatch):
    assert client.get('/health').json()['engine'] == 'still'
    assert streams(client.get('/idle?format=webm').content)[0] == {'video': 'vp8'}
    r = client.post('/lipsync', content=wav(1.0))
    assert r.headers['content-type'] == 'video/mp4' and int(r.headers['x-render-ms']) >= 0
    assert client.post('/lipsync', content=b'').status_code == 400
    assert client.post('/lipsync?format=gif', content=wav()).status_code == 400
    assert client.post('/lipsync', content=b'not audio at all').status_code == 400
    monkeypatch.setattr(av_server, 'MAX_AUDIO_BYTES', 100)
    assert client.post('/lipsync', content=wav()).status_code == 413


def test_server_refuses_web_pages_and_other_hosts(idle):
    app = av_server.create_app(av_server.StillEngine(idle))
    assert TestClient(app, base_url='http://127.0.0.1').get('/health', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert TestClient(app, base_url='http://apex-pc.example').get('/health').status_code == 403


@pytest.fixture
def running(idle):
    """A real avatar server on a free loopback port, as on the PC."""
    import uvicorn
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(av_server.create_app(av_server.StillEngine(idle)), host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(0.05)
    yield f'http://127.0.0.1:{port}'
    server.should_exit = True; thread.join(5)


@pytest.fixture
def apex(monkeypatch):
    import config
    from dashboard import companion
    app = FastAPI(); app.include_router(companion.router)
    return TestClient(app), config


def test_apex_says_when_the_avatar_is_off(apex, monkeypatch):
    client, config = apex
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); closed = s.getsockname()[1]
    monkeypatch.setattr(config, 'AVATAR_URL', f'http://127.0.0.1:{closed}')
    status = client.get('/api/avatar/status').json()
    assert status['available'] is False and 'Start-Apex-Video-Avatar' in status['reason']
    assert client.get('/api/avatar/idle').status_code == 503
    assert client.post('/api/avatar/lipsync', content=wav()).status_code == 503


def test_apex_passes_requests_through(apex, running, monkeypatch):
    client, config = apex
    monkeypatch.setattr(config, 'AVATAR_URL', running)
    assert client.get('/api/avatar/status').json() == {'available': True, 'engine': 'still', 'fps': 25.0}
    idle = client.get('/api/avatar/idle?format=webm')
    assert idle.headers['content-type'] == 'video/webm'
    clip = client.post('/api/avatar/lipsync?format=webm', content=wav(1.0))
    assert clip.status_code == 200 and streams(clip.content)[0] == {'video': 'vp8', 'audio': 'opus'}
    assert client.get('/api/avatar/idle?format=gif').status_code == 400
    foreign = client.post('/api/avatar/lipsync', content=wav(), headers={'Origin': 'https://evil.example'})
    assert foreign.status_code == 403


def test_make_idle_resamples_any_video_to_25_fps_and_caps_length(tmp_path):
    frames = [np.full((1000, 600, 3), i * 10 % 255, np.uint8) for i in range(60)]      # 2 s at 30 fps, tall
    import av
    src = tmp_path / 'phone.mp4'
    out = io.BytesIO()
    with av.open(out, 'w', format='mp4', options={'movflags': 'frag_keyframe+empty_moov'}) as box:
        s = box.add_stream('libx264', rate=30); s.width, s.height, s.pix_fmt = 600, 1000, 'yuv420p'
        for f in frames:
            for p in s.encode(av.VideoFrame.from_ndarray(f, format='bgr24')): box.mux(p)
        for p in s.encode(): box.mux(p)
    src.write_bytes(out.getvalue())
    count = av_server.make_idle(src, tmp_path / 'apex-idle.mp4', seconds=1.5)
    got, fps = av_server.read_frames(tmp_path / 'apex-idle.mp4')
    assert fps == 25 and len(got) == count and 36 <= count <= 39                       # 1.5 s at 25 fps
    assert got[0].shape[0] == 720                                                       # scaled down to 720 tall


def test_setup_tracks_each_step_and_never_uses_a_mirror(tmp_path):
    from scripts import setup_video_avatar as setup
    state = setup.status(tmp_path)
    assert not any(state[k] for k in ('python', 'musetalk', 'packages', 'weights'))
    models = tmp_path / 'MuseTalk' / 'models'
    (tmp_path / 'MuseTalk' / 'musetalk').mkdir(parents=True)
    for repo, files, folder in setup.WEIGHTS:
        for f in files:
            path = models / folder / f; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'w')
    (tmp_path / 'env').mkdir(); (tmp_path / 'env' / '.apex-installed').write_text(setup.COMMIT)
    state = setup.status(tmp_path)
    assert state['musetalk'] and state['weights'] and state['packages'] and state['missing_weights'] == []
    (tmp_path / 'env' / '.apex-installed').write_text('an older commit')
    assert not setup.status(tmp_path)['packages']                                       # a new pin reinstalls
    source = (setup.Path(setup.__file__)).read_text()
    assert 'HF_ENDPOINT", None' in source and 'hf-mirror.com' not in source.split('"""', 2)[2]
    assert len(setup.COMMIT) == 40
