"""No model downloads or paid requests: exercise local routing and upload guards."""
from types import SimpleNamespace as NS
import threading
import pytest
import config
from voice import browser_stt, stt


def test_local_uses_whisper_with_vad_and_no_openai_key(monkeypatch):
    monkeypatch.setattr(config,'OPENAI_API_KEY','')
    monkeypatch.setattr(browser_stt,'decode_bounded',lambda data:'decoded-audio')
    seen={}
    def transcribe(audio,**kwargs):
        seen.update(kwargs);assert audio=='decoded-audio'
        return iter([NS(text='Hello Apex',no_speech_prob=.1),NS(text='noise',no_speech_prob=.9)]),None
    monkeypatch.setattr(stt,'_get_model',lambda:NS(transcribe=transcribe))
    assert browser_stt.transcribe(b'fake','speech.webm','local')=='Hello Apex'
    assert seen['vad_filter'] is True
    assert seen['condition_on_previous_text'] is False


def test_openai_is_explicit_and_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(browser_stt,'decode_bounded',lambda data:'decoded')
    monkeypatch.setattr(config,'OPENAI_API_KEY','')
    with pytest.raises(ValueError,match='needs OPENAI_API_KEY'):
        browser_stt.transcribe(b'fake','speech.webm','openai')


def test_browser_endpoint_limits_and_auth(monkeypatch, test_db):
    from fastapi.testclient import TestClient
    from dashboard import server, companion
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','local-test')
    c=TestClient(server.app)
    assert c.post('/api/companion/transcribe',content=b'fake').status_code==401
    c.headers['Authorization']='Bearer local-test'
    assert c.post('/api/companion/transcribe',content=b'fake',headers={'Origin':'https://other.test'}).status_code==403
    assert c.post('/api/companion/transcribe',content=b'').status_code==400
    assert c.post('/api/companion/transcribe?engine=wrong',content=b'fake').status_code==400
    assert c.post('/api/companion/transcribe',content=b'x'*8_000_001).status_code==413
    calls=[]
    monkeypatch.setattr(browser_stt,'transcribe',lambda *a:calls.append(a) or 'Hello')
    assert c.post('/api/companion/transcribe',content=b'fake',headers={'Content-Type':'audio/mp4'}).json()=={'text':'Hello'}
    assert calls==[(b'fake','speech.mp4','local')]
    sem=threading.BoundedSemaphore(1);sem.acquire()
    monkeypatch.setattr(companion,'_stt_slots',sem)
    assert c.post('/api/companion/transcribe',content=b'fake').status_code==429


def test_decode_duration_is_bounded_without_whisper(monkeypatch):
    import sys
    import numpy as np
    frames=[NS(to_ndarray=lambda:np.zeros(16000,dtype=np.int16))]*66
    class Container:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def decode(self,**kw):return iter(frames)
    monkeypatch.setitem(sys.modules,'av',NS(open=lambda stream:Container(), AudioResampler=lambda **kw:NS(resample=lambda frame:[frame])))
    with pytest.raises(ValueError,match='65-second'):
        browser_stt.decode_bounded(b'fake')
