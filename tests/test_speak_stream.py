"""/api/speak/stream: Celine's audio passed through as it is made.

Both servers are real processes-in-threads on real ports (a buffering test
client would hide the one property this route exists for), the voice engine
is fake.
"""
from __future__ import annotations

from pathlib import Path
from scripts.voice_library import Voice as _Voice
_CELINE = _Voice('celine', 'CELINE', Path('celine.ogg'), 'Hello.', 1.0, folder=False)
_LIB = lambda: [_CELINE]


import importlib.util
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("qwen_fast_server_t", ROOT / "scripts" / "qwen_fast_server.py")
fast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fast)


class FakeVoice:
    sample_rate = 24000

    def __init__(self, gate=None):
        self.gate = gate

    def stream(self, text, voice=None):
        for i in range(3):
            yield np.full(2400, 0.25, dtype=np.float32)
            if i == 0 and self.gate is not None:
                self.gate.wait(30)   # never gives up by itself: a buffering server must hang


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app):
    import uvicorn
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(300):
        try:
            socket.create_connection(("127.0.0.1", port), 0.1).close()
            return server, port
        except OSError:
            time.sleep(0.02)
    raise RuntimeError("server did not start")


@pytest.fixture
def apex(monkeypatch):
    import config
    from dashboard import server as dash
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
    srv, port = _serve(dash.app)
    yield f"http://127.0.0.1:{port}", monkeypatch
    srv.should_exit = True


def voice_at(monkeypatch, voice):
    import config
    srv, port = _serve(fast.create_app(voice, _LIB))
    monkeypatch.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{port}", raising=False)
    return srv


def speak(base, body, origin=True):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = base
    req = urllib.request.Request(base + "/api/speak/stream", data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    # 3 s: if the first chunk has not arrived by then, something buffered it.
    return urllib.request.urlopen(req, timeout=3)


def test_audio_passes_through_before_the_section_is_finished(apex):
    base, mp = apex
    gate = threading.Event()
    voice_at(mp, FakeVoice(gate))
    with speak(base, {"text": "Hello there.", "engine": "voicebox", "profile": "celine"}) as r:
        assert r.status == 200
        assert r.headers["X-Sample-Rate"] == "24000"
        first = r.read(4800)
        assert len(first) == 4800 and not gate.is_set(), "Apex buffered the stream"
        gate.set()
        assert len(r.read()) == 2 * 4800


def test_a_voice_server_that_cannot_stream_gets_404_so_the_page_falls_back(apex):
    """The original Celine server and the Voicebox app have no /generate/pcm."""
    base, mp = apex
    from fastapi import FastAPI
    plain = FastAPI()

    @plain.get("/health")
    def health():
        return {"status": "healthy", "model_loaded": True}
    import config
    srv, port = _serve(plain)
    mp.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{port}", raising=False)
    with pytest.raises(urllib.error.HTTPError) as e:
        speak(base, {"text": "Hi.", "engine": "voicebox"})
    assert e.value.code == 404


def test_no_voice_server_at_all_is_404_too(apex):
    base, mp = apex
    import config
    mp.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{_port()}", raising=False)
    with pytest.raises(urllib.error.HTTPError) as e:
        speak(base, {"text": "Hi.", "engine": "voicebox"})
    assert e.value.code == 404


def test_busy_is_passed_on_not_turned_into_a_fallback(apex):
    base, mp = apex
    gate = threading.Event()
    voice_at(mp, FakeVoice(gate))
    with speak(base, {"text": "One.", "engine": "voicebox"}) as r:
        r.read(4800)
        with pytest.raises(urllib.error.HTTPError) as e:
            speak(base, {"text": "Two.", "engine": "voicebox"})
        assert e.value.code == 409
        gate.set()
        r.read()


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "x" * 4001}, {"text": "hi", "engine": "openai"},
                                  {"text": "hi", "profile": 5}])
def test_bad_requests(apex, body):
    base, mp = apex
    voice_at(mp, FakeVoice())
    with pytest.raises(urllib.error.HTTPError) as e:
        speak(base, body)
    assert e.value.code == 400


def test_the_page_can_ask_before_the_first_reply(apex):
    """GET tells the page whether to plan for streaming (no comma split,
    merged sections) before it has spoken a word."""
    base, mp = apex
    voice_at(mp, FakeVoice())
    data = json.loads(urllib.request.urlopen(base + "/api/speak/stream", timeout=3).read())
    assert data == {"streaming": True, "sample_rate": 24000}
    import config
    mp.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{_port()}", raising=False)
    data = json.loads(urllib.request.urlopen(base + "/api/speak/stream", timeout=3).read())
    assert data == {"streaming": False, "sample_rate": None}


class Trickler:
    """A voice server that answers too slowly on purpose: `health` decides how
    /health answers, `pcm` how /generate/pcm does."""

    def __init__(self, health='trickle', pcm='trickle'):
        self.health, self.pcm, self.stop = health, pcm, threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            request = conn.recv(65536).decode('latin-1')
            try:
                if request.startswith('GET /health'):
                    if self.health == 'ok':
                        body = b'{"streaming": true, "sample_rate": 24000}'
                        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n'
                                     b'Content-Length: %d\r\n\r\n' % len(body) + body)
                        return
                    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n')
                    while not self.stop.wait(0.1):
                        conn.sendall(b' ')            # one byte at a time, forever
                    return
                conn.sendall(b'HTTP/1.1 200 OK\r\nX-Sample-Rate: 24000\r\n'
                             b'Content-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
                while not self.stop.wait(0.1):
                    if self.pcm == 'trickle':
                        conn.sendall(b'2\r\n\x00\x00\r\n')
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.sock.close()


@pytest.fixture
def trickler(apex):
    import config
    from voice import voicebox
    base, mp = apex
    servers = []
    def make(**kw):
        t = Trickler(**kw)
        servers.append(t)
        mp.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{t.port}", raising=False)
        return t
    mp.setattr(voicebox, "STREAM_HEALTH_DEADLINE", 0.5)
    mp.setattr(voicebox, "STREAM_STALL", 0.5)
    mp.setattr(voicebox, "stream_deadline", lambda text: 1.5)
    yield base, make
    for t in servers:
        t.close()


def test_a_trickling_health_answer_does_not_hold_the_page(trickler):
    base, make = trickler
    make(health='trickle')
    started = time.monotonic()
    data = json.loads(urllib.request.urlopen(base + "/api/speak/stream", timeout=5).read())
    assert data["streaming"] is False and time.monotonic() - started < 3


@pytest.mark.parametrize('pcm', ['trickle', 'silent'])
def test_a_trickling_or_silent_voice_stream_ends(trickler, pcm):
    """A voice server sending two bytes every 0.1 s (or nothing) after its
    headers used to keep the request open for as long as it liked."""
    base, make = trickler
    make(health='ok', pcm=pcm)
    started = time.monotonic()
    req = urllib.request.Request(base + "/api/speak/stream", method="POST",
                                 data=json.dumps({"text": "Hello.", "profile": "celine"}).encode(),
                                 headers={"Content-Type": "application/json", "Origin": base})
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()
    assert time.monotonic() - started < 5
