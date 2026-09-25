"""/api/speak/stream: Celine's audio passed through as it is made.

Both servers are real processes-in-threads on real ports (a buffering test
client would hide the one property this route exists for), the voice engine
is fake.
"""
from __future__ import annotations

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

    def stream(self, text):
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
    srv, port = _serve(fast.create_app(voice))
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
