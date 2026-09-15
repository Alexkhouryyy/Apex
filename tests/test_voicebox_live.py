"""The voice chain against a real HTTP server speaking Voicebox's protocol.

`tests/test_voicebox.py` monkeypatches the transport, which proves the logic
and cannot prove the wire. Nothing there exercises the actual httpx client, the
real streaming read, the WAV validation against real bytes, or the JSON body
Apex sends to `/generate/stream`.

So this file stands up a real ASGI server on a real port that answers
`/profiles` and `/generate/stream` the way Voicebox does, and drives the
dashboard's own `/api/speak` end to end. Everything up to the audio itself is
covered; whether Qwen sounds right is a human's job on a machine with Voicebox
installed, and no test here claims otherwise.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import socket
import struct
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient


def _wav(seconds: float = 0.2, freq: int = 440, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds))))
    return buf.getvalue()


class _FakeVoicebox:
    """Voicebox's three endpoints, recording what it was asked for."""

    def __init__(self):
        self.profiles = [
            {"id": "p-ryan", "name": "Apex Qwen Local", "language": "en",
             "voice_type": "preset", "preset_engine": "qwen_custom_voice",
             "preset_voice_id": "Ryan", "default_engine": "qwen_custom_voice"},
            {"id": "p-clone", "name": "Alex Clone", "language": "en",
             "voice_type": "clone", "default_engine": "qwen"},
            {"id": "p-import", "name": "Imported Thing", "language": "en",
             "voice_type": "import"},
        ]
        self.seen: list[dict] = []
        self.audio = _wav()

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        self.seen.append({"method": scope["method"], "path": scope["path"],
                          "body": json.loads(body) if body else None})

        async def reply(status, payload, ctype=b"application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", ctype)]})
            await send({"type": "http.response.body", "body": data})

        if scope["path"] == "/profiles" and scope["method"] == "GET":
            return await reply(200, self.profiles)
        if scope["path"] == "/profiles" and scope["method"] == "POST":
            created = {"id": "p-new", "voice_type": "preset",
                       "preset_engine": "qwen_custom_voice",
                       **(self.seen[-1]["body"] or {})}
            self.profiles.append(created)
            return await reply(200, created)
        if scope["path"] == "/generate/stream":
            return await reply(200, self.audio, b"audio/wav")
        return await reply(404, {"detail": "no such endpoint"})

    def last_generate(self) -> dict:
        return [s for s in self.seen if s["path"] == "/generate/stream"][-1]["body"]


@pytest.fixture
def voicebox(monkeypatch):
    import uvicorn
    import config

    fake = _FakeVoicebox()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(fake, host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        with contextlib.suppress(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        time.sleep(0.05)
    else:
        pytest.fail("the fake Voicebox never came up")

    monkeypatch.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(config, "VOICEBOX_PROFILE", "")
    monkeypatch.setattr(config, "VOICEBOX_SPEAKER", "Ryan")
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "t0ken")
    fake.stop = lambda: setattr(server, "should_exit", True)
    yield fake
    server.should_exit = True


@pytest.fixture
def client():
    from dashboard.server import app
    return TestClient(app)


AUTH = {"Authorization": "Bearer t0ken", "Origin": "http://testserver"}


def speak(client, **body):
    return client.post("/api/speak", headers=AUTH,
                       json={"text": "Apex speaking through local Qwen.",
                             "engine": "voicebox", **body})


class TestTheWholeChain:

    def test_the_reply_comes_back_as_a_real_playable_wav(self, voicebox, client):
        """Parsed with the `wave` module, not just checked for a RIFF header.
        A truncated or malformed stream passes a prefix check and fails here."""
        r = speak(client, profile="")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/wav"
        with wave.open(io.BytesIO(r.content)) as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 16000
            assert w.getnframes() > 0

    def test_apex_asks_for_qwen_by_name(self, voicebox, client):
        """The body Apex sends is the contract with Voicebox. A silent change
        to the engine or model size would still return audio — from the wrong
        model."""
        speak(client, profile="")
        sent = voicebox.last_generate()
        assert sent["engine"] == "qwen_custom_voice"
        assert sent["model_size"] == "1.7B"
        assert sent["profile_id"] == "p-ryan"
        assert sent["personality"] is False

    def test_the_default_preset_is_reused_not_recreated(self, voicebox, client):
        """"Never alter existing cloned voices" — so a second turn must not
        create a second 'Apex Qwen Local'."""
        speak(client, profile="")
        speak(client, profile="")
        created = [s for s in voicebox.seen
                   if s["path"] == "/profiles" and s["method"] == "POST"]
        assert created == []

    def test_a_missing_preset_is_created_once(self, voicebox, client):
        voicebox.profiles = [p for p in voicebox.profiles if p["id"] != "p-ryan"]
        assert speak(client, profile="").status_code == 200
        created = [s for s in voicebox.seen
                   if s["path"] == "/profiles" and s["method"] == "POST"]
        assert len(created) == 1
        assert created[0]["body"]["preset_voice_id"] == "Ryan"

    def test_a_chosen_clone_is_used_as_given(self, voicebox, client):
        assert speak(client, profile="p-clone").status_code == 200
        assert voicebox.last_generate()["profile_id"] == "p-clone"

    def test_the_picker_hides_imported_voices(self, voicebox, client):
        r = client.get("/api/voicebox/profiles", headers=AUTH)
        assert r.status_code == 200
        assert [p["name"] for p in r.json()["profiles"]] == \
            ["Apex Qwen Local", "Alex Clone"]

    def test_an_ambiguous_name_is_refused_rather_than_guessed(self, voicebox, client):
        voicebox.profiles.append(dict(voicebox.profiles[1], id="p-clone2"))
        r = speak(client, profile="Alex Clone")
        assert r.status_code == 503
        assert "ambiguous" in r.json()["error"]

    def test_corrupt_audio_is_refused_not_played(self, voicebox, client):
        """A browser handed non-WAV bytes fails at playback with nothing
        useful to say. Apex checks the RIFF/WAVE header itself."""
        voicebox.audio = b"this is not audio at all"
        r = speak(client, profile="")
        assert r.status_code == 503
        assert "invalid WAV" in r.json()["error"]


class TestItNeverQuietlySpeaksInAnotherVoice:
    """`voice/voicebox.py` line one: "No cloud fallback or model downloads in
    Apex." A silent fallback is how you end up paying a cloud provider for
    speech while believing it runs locally."""

    def test_voicebox_being_closed_is_an_error_not_a_substitution(
            self, voicebox, client):
        voicebox.stop()
        time.sleep(0.5)
        r = speak(client, profile="")
        assert r.status_code == 503
        assert "Keep Voicebox open" in r.json()["error"]
        assert not r.headers["content-type"].startswith("audio")

    def test_only_a_local_address_is_accepted(self, monkeypatch):
        """The bridge refuses to be pointed at another machine, so a stray
        VOICEBOX_URL cannot send what Apex says to somebody else's server."""
        import config
        from voice import voicebox as vb
        for bad in ("http://example.com:17493", "https://127.0.0.1:17493",
                    "http://user:pw@127.0.0.1:17493", "http://127.0.0.1:17493/x"):
            monkeypatch.setattr(config, "VOICEBOX_URL", bad)
            with pytest.raises(ValueError):
                vb.base_url()
