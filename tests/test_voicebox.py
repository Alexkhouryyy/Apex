import asyncio
import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

import config
from voice import voicebox
from dashboard import server


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b'\0\0' * 100)
    return out.getvalue()


def mock_client(monkeypatch, handler):
    monkeypatch.setattr(voicebox, 'client', lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url='http://127.0.0.1:17493'))
    monkeypatch.setattr(config, 'VOICEBOX_PROFILE', '')
    monkeypatch.setattr(config, 'VOICEBOX_SPEAKER', 'Ryan')


def test_preset_creation_and_local_audio(monkeypatch):
    calls = []
    def handle(req):
        import json
        body = json.loads(req.content) if req.content else None
        calls.append((req.url.path, body))
        if req.url.path == '/profiles':
            return httpx.Response(200, json=[] if req.method == 'GET' else {
                'id': 'preset1', 'name': 'Apex Qwen Local', 'voice_type': 'preset',
                'preset_engine': 'qwen_custom_voice', 'language': 'en'})
        assert body['engine'] == 'qwen_custom_voice'
        assert body['model_size'] == '1.7B' and body['personality'] is False
        assert body['text'] == 'Hello Alex'
        return httpx.Response(200, content=wav_bytes())
    mock_client(monkeypatch, handle)
    assert asyncio.run(voicebox.synthesize('Hello Alex')) == wav_bytes()
    assert calls[1][1]['preset_voice_id'] == 'Ryan'


def test_selected_clone_uses_base_without_creating_profile(monkeypatch):
    def handle(req):
        if req.url.path == '/profiles':
            assert req.method == 'GET'
            return httpx.Response(200, json=[{'id': 'celine', 'name': 'Celine', 'voice_type': 'cloned', 'language': 'en'}])
        import json
        assert json.loads(req.content)['engine'] == 'qwen'
        return httpx.Response(200, content=wav_bytes())
    mock_client(monkeypatch, handle)
    assert asyncio.run(voicebox.synthesize('Hi', 'celine')) == wav_bytes()


@pytest.mark.parametrize('url', ['https://example.com', 'http://192.168.1.3:17493', 'http://localhost@evil.test', 'http://localhost/path'])
def test_reject_remote_voice_server(monkeypatch, url):
    monkeypatch.setattr(config, 'VOICEBOX_URL', url)
    with pytest.raises(ValueError): voicebox.base_url()


def test_failure_releases_gate_and_never_falls_back(monkeypatch):
    def handle(req): raise httpx.ConnectError('offline', request=req)
    mock_client(monkeypatch, handle)
    for _ in range(2):
        with pytest.raises(ValueError, match='Keep Voicebox open'):
            asyncio.run(voicebox.synthesize('hello'))


def test_speech_route_auth_origin_and_wav(monkeypatch):
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'test-voice')
    monkeypatch.setattr(config, 'OPENAI_API_KEY', '')
    async def synth(text, profile):
        assert (text, profile) == ('hello', 'celine')
        return wav_bytes()
    monkeypatch.setattr(voicebox, 'synthesize', synth)
    with TestClient(server.app) as c:
        assert c.post('/api/speak', json={'text': 'hello'}).status_code in (401,403)
        c.headers['Authorization'] = 'Bearer test-voice'
        body = {'text': 'hello', 'engine': 'voicebox', 'profile': 'celine'}
        assert c.post('/api/speak', json=body, headers={'Origin': 'https://evil.test'}).status_code == 403
        r = c.post('/api/speak', json=body)
        assert r.status_code == 200 and r.content == wav_bytes()
        assert r.headers['content-type'] == 'audio/wav'
        assert c.post('/api/speak', json={'text': 'x'*4001}).status_code == 400
        assert c.post('/api/speak', json=[]).status_code == 400


def test_invalid_audio_rejected(monkeypatch):
    def handle(req):
        if req.url.path == '/profiles':
            return httpx.Response(200, json=[{'id':'one', 'name':'One', 'voice_type':'cloned'}])
        return httpx.Response(200, content=b'<html>not audio</html>')
    mock_client(monkeypatch, handle)
    with pytest.raises(ValueError, match='invalid WAV'):
        asyncio.run(voicebox.synthesize('hello', 'one'))


def test_ambiguous_profile_never_picks_arbitrarily(monkeypatch):
    mock_client(monkeypatch, lambda req: httpx.Response(200, json=[
        {'id':'one', 'name':'Celine'}, {'id':'two', 'name':'Celine'}]))
    with pytest.raises(ValueError, match='ambiguous'):
        asyncio.run(voicebox.synthesize('hello', 'Celine'))


def test_busy_generation_rejected():
    voicebox._gate.acquire()
    try:
        with pytest.raises(ValueError, match='already generating'):
            asyncio.run(voicebox.synthesize('hello'))
    finally:
        voicebox._gate.release()


def test_terminal_uses_local_wav_without_pyttsx3(monkeypatch):
    import sys
    from types import SimpleNamespace
    from voice import tts
    calls = []
    async def synth(text): return wav_bytes()
    monkeypatch.setattr(voicebox, 'synthesize', synth)
    monkeypatch.setattr(config, 'TTS_ENGINE', 'voicebox')
    monkeypatch.setattr(tts, '_get_pyttsx3', lambda: pytest.fail('Unexpected system voice'))
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(
        play=lambda data, rate: calls.append((data.shape, rate)),
        get_stream=lambda: SimpleNamespace(active=False), stop=lambda: None))
    assert tts.speak('Hello', interruptible=False)
    assert calls == [((100, 1), 24000)]


def test_setup_preserves_brain_settings(monkeypatch, tmp_path):
    from scripts import setup_voicebox
    monkeypatch.setattr(setup_voicebox, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['setup_voicebox.py'])
    env = tmp_path / '.env'
    env.write_text('AGENT_MODEL=deepseek-flash\nMEMORY_DB_PATH=existing.db\nTTS_ENGINE=openai\n')
    async def synth(text): return wav_bytes()
    monkeypatch.setattr(setup_voicebox, 'synthesize', synth)
    assert setup_voicebox.main() == 0
    assert 'AGENT_MODEL=deepseek-flash' in env.read_text()
    assert 'MEMORY_DB_PATH=existing.db' in env.read_text()
    assert "TTS_ENGINE='voicebox'" in env.read_text()
    assert len(list(tmp_path.glob('.env.voicebox-backup-*'))) == 1


def test_setup_failure_does_not_change_env(monkeypatch, tmp_path):
    from scripts import setup_voicebox
    monkeypatch.setattr(setup_voicebox, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['setup_voicebox.py'])
    env = tmp_path / '.env'; env.write_text('TTS_ENGINE=openai\n')
    async def synth(text): raise ValueError('Voicebox offline')
    monkeypatch.setattr(setup_voicebox, 'synthesize', synth)
    assert setup_voicebox.main() == 1
    assert env.read_text() == 'TTS_ENGINE=openai\n'
