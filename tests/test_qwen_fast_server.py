"""The streaming Celine server, driven with a fake engine.

The real engine needs the laptop's GPU; what is tested here is the part that
decides whether streaming actually streams: audio leaves before the section
is finished, one generation holds the GPU at a time, a failure frees it, and
the WAV route still works for callers that cannot stream.
"""
from __future__ import annotations

import importlib.util
import io
import threading
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("qwen_fast_server", ROOT / "scripts" / "qwen_fast_server.py")
fast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fast)


class FakeVoice:
    sample_rate = 24000

    def __init__(self, chunks=3, gate=None, fail_after=None):
        self.chunks, self.gate, self.fail_after = chunks, gate, fail_after
        self.calls = []

    def stream(self, text):
        self.calls.append(text)
        for i in range(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("GPU fell over")
            yield np.full(2400, 0.5 if i % 2 else -0.5, dtype=np.float32)   # 0.1 s each
            if i == 0 and self.gate is not None:
                self.gate.wait(30)   # never gives up by itself: a buffering server must hang            # the rest waits until the test says so


def client(voice):
    return TestClient(fast.create_app(voice), base_url="http://127.0.0.1")


class live:
    """A real server on a real port. Starlette's TestClient buffers a
    streamed body whole, which would hide exactly what streaming is for."""

    def __init__(self, voice):
        import socket
        import uvicorn
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(fast.create_app(voice), host="127.0.0.1",
                                                    port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        import socket
        import time
        self.thread.start()
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                return f"http://127.0.0.1:{self.port}"
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("server did not start")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)


def open_pcm(base, text):
    import json
    import urllib.request
    req = urllib.request.Request(base + "/generate/pcm", data=json.dumps({"text": text}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    # 3 s: if the first chunk has not arrived by then, something buffered it.
    return urllib.request.urlopen(req, timeout=3)


def test_health_says_it_streams():
    h = client(FakeVoice()).get("/health").json()
    assert h["model_loaded"] and h["streaming"] is True and h["sample_rate"] == 24000
    assert h["service"] == "apex-qwen"


def test_pcm_arrives_before_the_section_is_finished():
    """The whole point: the first audio leaves while the engine is still
    generating the rest."""
    gate = threading.Event()
    voice = FakeVoice(chunks=3, gate=gate)
    with live(voice) as base, open_pcm(base, "Hello there.") as r:
        assert r.status == 200
        assert r.headers["X-Sample-Rate"] == "24000"
        assert r.headers["X-Audio-Format"] == "pcm_s16le"
        first = r.read(2400 * 2)                 # blocks until that much has arrived
        assert len(first) == 2400 * 2
        assert not gate.is_set(), "first chunk waited for the whole section"
        gate.set()
        rest = r.read()
    assert len(first) + len(rest) == 3 * 2400 * 2
    samples = np.frombuffer(first, dtype="<i2")
    assert samples[0] == -16383, "float -0.5 must become PCM -16383"


def test_one_generation_holds_the_gpu_and_releases_it():
    import urllib.error
    gate = threading.Event()
    voice = FakeVoice(chunks=2, gate=gate)
    with live(voice) as base:
        with open_pcm(base, "One.") as r:
            r.read(2400 * 2)
            try:
                open_pcm(base, "Two.").read()
                raise AssertionError("a second generation got the GPU")
            except urllib.error.HTTPError as e:
                assert e.code == 409
            gate.set()
            r.read()
        with open_pcm(base, "Three.") as r:
            assert r.status == 200, "the GPU was not released after the first finished"
            r.read()


def test_a_failure_mid_stream_ends_it_and_frees_the_gpu():
    voice = FakeVoice(chunks=3, fail_after=1)
    with live(voice) as base:
        with open_pcm(base, "Boom.") as r:
            data = r.read()
        assert len(data) == 2400 * 2, "what was made before the failure is delivered, then it ends"
        voice.fail_after = None
        with open_pcm(base, "Again.") as r:
            assert r.status == 200 and len(r.read()) == 3 * 2400 * 2


def test_the_wav_route_still_works_for_callers_that_cannot_stream():
    r = client(FakeVoice(chunks=3)).post("/generate/stream", json={"text": "Hi."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 3 * 2400


def test_guards():
    c = client(FakeVoice())
    assert c.post("/generate/pcm", json={"text": ""}).status_code == 400
    assert c.post("/generate/pcm", json={"text": "x" * 4001}).status_code == 400
    assert c.post("/generate/pcm", json={"text": "hi", "profile_id": "other"}).status_code == 400
    assert c.post("/generate/pcm", content=b"{nope").status_code == 400
    assert c.post("/generate/pcm", json={"text": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403
    remote = TestClient(fast.create_app(FakeVoice()), base_url="http://192.168.1.5")
    assert remote.get("/health").status_code == 403


def test_pcm16_clips_instead_of_wrapping():
    out = np.frombuffer(fast.pcm16(np.array([2.0, -2.0, 0.0], dtype=np.float32)), dtype="<i2")
    assert list(out) == [32767, -32767, 0]


def test_the_fast_launcher_uses_the_fast_environment_and_server(tmp_path, monkeypatch):
    """--fast must start qwen_fast_server.py in apex-qwen-fast-env, and say
    how to get that environment when it is missing."""
    spec2 = importlib.util.spec_from_file_location("run_apex_qwen_t", ROOT / "scripts" / "run_apex_qwen.py")
    launcher = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(launcher)
    monkeypatch.setattr(launcher.Path, "home", staticmethod(lambda: tmp_path))
    import pytest
    with pytest.raises(RuntimeError, match="apex-qwen-fast-env.*Test-Apex-Fast-Voice"):
        launcher.main(["--fast"])
    with pytest.raises(RuntimeError, match="apex-qwen-env"):
        launcher.main([])
    src = (ROOT / "scripts" / "run_apex_qwen.py").read_text()
    assert "'scripts/qwen_fast_server.py'" in src
    cmd = (ROOT / "Start-Apex-Celine-Fast.cmd").read_text()
    assert "run_apex_qwen.py --fast" in cmd
