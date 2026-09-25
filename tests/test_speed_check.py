"""scripts/speed_check.py times the brain and the voice on the user's laptop;
here it runs against a fake stream and a real local HTTP server."""
from __future__ import annotations

import importlib.util
import io
import json
import socket
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("speed_check", ROOT / "scripts" / "speed_check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


class FakeClient:
    def __init__(self):
        self.kwargs = None
        outer = self

        class Stream:
            def __enter__(self):
                return iter([SimpleNamespace(type="message_start"),
                             SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(text="Hi")),
                             SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(text=" there."))])

            def __exit__(self, *a):
                return False

        class Messages:
            def stream(self, **kwargs):
                outer.kwargs = kwargs
                return Stream()
        self.messages = Messages()


def test_the_brain_is_timed_and_tools_are_sent_when_asked():
    c = FakeClient()
    out = sc.time_brain(c, "deepseek-chat", [{"name": "x"}])
    assert out["text"] == "Hi there." and out["first_word"] is not None
    assert c.kwargs["tools"] == [{"name": "x"}] and c.kwargs["model"] == "deepseek-chat"
    sc.time_brain(c, "deepseek-chat", None)
    assert "tools" not in c.kwargs


def test_the_voice_is_timed_against_how_long_it_plays():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 32000)          # two seconds
    wav, seen = buf.getvalue(), {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        v = sc.time_voice(f"http://127.0.0.1:{port}")
    finally:
        srv.shutdown(); srv.server_close()
    assert seen["path"] == "/generate/stream" and seen["body"]["profile_id"] == "celine"
    assert abs(v["plays"] - 2.0) < 1e-6 and v["ratio"] < 0.5


def test_verdicts():
    assert sc.verdict_brain(None) == "no words came back"
    assert sc.verdict_brain(1.0) == "fine" and sc.verdict_brain(30) == "TOO SLOW"
    assert sc.verdict_voice(0.3).startswith("fine")
    assert sc.verdict_voice(4.0).startswith("TOO SLOW")
