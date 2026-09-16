import importlib.util
import io
from pathlib import Path
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('qwen_server', Path(__file__).parents[1] / 'scripts/qwen_server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class FakeVoice:
    def __init__(self):
        self.calls = []
    def generate(self, text):
        self.calls.append(text)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b'\0\0' * 240)
        return buf.getvalue()

def client(voice):
    return TestClient(server.create_app(voice), base_url='http://127.0.0.1')

def test_protocol_and_repeated_generation():
    voice = FakeVoice()
    with client(voice) as c:
        assert c.get('/health').json()['model_loaded']
        profile = c.get('/profiles').json()[0]
        for text in ['Hello', 'Second reply']:
            r = c.post('/generate/stream', json=dict(text=text, profile_id=profile['id'], engine='qwen', model_size='1.7B'))
            assert r.status_code == 200
            assert r.content[:4] == b'RIFF'
            assert r.headers['content-type'] == 'audio/wav'
        assert voice.calls == ['Hello', 'Second reply']

def test_guards():
    voice = FakeVoice()
    with client(voice) as c:
        assert c.get('/profiles', headers={'origin':'https://evil.example'}).status_code == 403
        assert c.get('/profiles', headers={'host':'evil.example'}).status_code == 403
        for body in [[], {}, {'text':' '}, {'text':'x'*4001}, {'text':'hi', 'profile_id':'ryan'}]:
            assert c.post('/generate/stream', json=body).status_code == 400
        assert c.post('/generate/stream', content='x'*24001).status_code == 413
        assert voice.calls == []

def test_failure_releases_lock():
    voice = FakeVoice()
    original = voice.generate
    voice.generate = lambda text: (_ for _ in ()).throw(RuntimeError('GPU failed'))
    with client(voice) as c:
        assert c.post('/generate/stream', json={'text':'hello'}).status_code == 503
        voice.generate = original
        assert c.post('/generate/stream', json={'text':'hello'}).status_code == 200

def test_overlapping_generation_is_rejected():
    entered, release = threading.Event(), threading.Event()
    voice = FakeVoice()
    original = voice.generate
    def blocking(text):
        entered.set()
        assert release.wait(5)
        return original(text)
    voice.generate = blocking
    with client(voice) as c, ThreadPoolExecutor() as pool:
        future = pool.submit(c.post, '/generate/stream', json={'text':'first'})
        try:
            assert entered.wait(5)
            assert c.post('/generate/stream', json={'text':'second'}).status_code == 409
        finally:
            release.set()
        assert future.result().status_code == 200
