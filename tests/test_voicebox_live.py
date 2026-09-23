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
            # 'cloned', not 'clone'. The first version of this fixture invented
            # the singular, which exercised the old catch-all 'else qwen' branch
            # rather than a real cloned voice — a test passing on a vocabulary
            # Voicebox does not use. tests/test_voicebox.py uses 'cloned'.
            {"id": "p-clone", "name": "Alex Clone", "language": "en",
             "voice_type": "cloned", "default_engine": "qwen"},
            {"id": "p-import", "name": "Imported Thing", "language": "en",
             "voice_type": "import"},
            # A preset for an engine that is not Qwen. This is the row that is
            # really hidden in practice, and the reason the picker's second
            # filter existed at all.
            {"id": "p-kokoro", "name": "Kokoro Preset", "language": "en",
             "voice_type": "preset", "preset_engine": "kokoro"},
            # A voice type Apex has never heard of. Voicebox owns this
            # vocabulary and its source is not readable from here.
            {"id": "p-future", "name": "Future Voice", "language": "en",
             "voice_type": "designed"},
        ]
        self.seen: list[dict] = []
        self.audio = _wav()
        self.created_override = None

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
            created = self.created_override or {
                "id": "p-new", "voice_type": "preset",
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

    def test_a_chosen_cloned_voice_is_used_as_given(self, voicebox, client):
        assert speak(client, profile="p-clone").status_code == 200
        assert voicebox.last_generate()["profile_id"] == "p-clone"

    def test_the_picker_shows_only_what_apex_can_speak_with(self, voicebox, client):
        r = client.get("/api/voicebox/profiles", headers=AUTH)
        assert r.status_code == 200
        assert [p["name"] for p in r.json()["profiles"]] == \
            ["Apex Qwen Local", "Alex Clone", "Future Voice"]


class TestThePickerAndTheSpeakerAgree:
    """The defect this class exists for: four places answered "is this profile
    usable" four different ways, and `synthesize` was not one of the two that
    agreed. A profile the picker refused to show would still speak if its id
    was sent straight to /api/speak — so the dropdown was not a statement about
    what works, it was a suggestion.

    They now share `voicebox.resolve_engine`, and these tests pin the property
    rather than the implementation: whatever the rule is, both sides apply it.
    """

    def test_every_hidden_profile_is_also_refused_by_speak(self, voicebox, client):
        shown = {p["id"] for p in
                 client.get("/api/voicebox/profiles", headers=AUTH).json()["profiles"]}
        hidden = [p["id"] for p in voicebox.profiles if p["id"] not in shown]
        assert hidden, "the fixture must contain something the picker hides"
        for pid in hidden:
            r = speak(client, profile=pid)
            assert r.status_code == 503, f"{pid} is hidden from the picker but speaks"
            assert not r.headers["content-type"].startswith("audio")

    def test_every_shown_profile_actually_speaks(self, voicebox, client):
        """The other direction, which matters just as much — a picker that
        offers a voice that then fails is the same bug wearing the other shoe."""
        shown = [p["id"] for p in
                 client.get("/api/voicebox/profiles", headers=AUTH).json()["profiles"]]
        for pid in shown:
            assert speak(client, profile=pid).status_code == 200, pid

    def test_a_hidden_profile_is_refused_by_name_too(self, voicebox, client):
        """Selection matches on id OR display name, so the name is a second
        door to the same set."""
        assert speak(client, profile="Imported Thing").status_code == 503

    def test_the_refusal_says_it_exists_and_why_not_that_it_is_missing(
            self, voicebox, client):
        """Three problems used to share one sentence. "Profile not found" for a
        voice sitting right there in Voicebox sends the user looking for
        something they have not lost — and a stale id in localStorage or a
        pinned VOICEBOX_PROFILE lands exactly here.

        The id deliberately does NOT contain its voice type. The first version
        of this test used `p-import` and asserted `"import" in err`, which is
        satisfied by "No Voicebox profile called 'p-import'" — the id carried
        the word, so the assertion passed with the fix reverted.
        """
        voicebox.profiles.append({"id": "p-42", "name": "Old Recording",
                                  "language": "en", "voice_type": "import"})
        err = speak(client, profile="p-42").json()["error"]
        assert "is a import voice" in err or "is a import" in err, err
        assert "cannot speak with" in err
        assert "No Voicebox profile" not in err

    def test_a_genuinely_absent_profile_still_says_not_found(self, voicebox, client):
        err = speak(client, profile="no-such-profile").json()["error"]
        assert "No Voicebox profile" in err

    def test_a_non_qwen_preset_is_hidden_and_refused(self, voicebox, client):
        """The rule that actually hides voices in practice. It used to live
        only inside the dashboard route, so it did not exist for synthesize,
        voice/tts.py or scripts/setup_voicebox.py."""
        shown = [p["name"] for p in
                 client.get("/api/voicebox/profiles", headers=AUTH).json()["profiles"]]
        assert "Kokoro Preset" not in shown
        assert speak(client, profile="p-kokoro").status_code == 503

    def test_an_undrivable_namesake_preset_is_not_reused(self, voicebox, client):
        """The auto-created default is found by NAME. A stale 'Apex Qwen Local'
        left behind pointing at some other engine must not be picked up just
        because the name matches — that would speak in a voice the picker
        would not have offered."""
        voicebox.profiles = [p for p in voicebox.profiles if p["id"] != "p-ryan"]
        # It has to pass the NAME/engine/speaker predicate and fail the usable
        # one, or the test proves nothing — a 'kokoro' namesake is rejected by
        # the name predicate anyway, which is how the first version of this
        # test passed with the fix reverted.
        voicebox.profiles.append({
            "id": "p-stale", "name": "Apex Qwen Local", "language": "en",
            "voice_type": "import", "preset_engine": "qwen_custom_voice",
            "preset_voice_id": "Ryan"})
        assert speak(client, profile="").status_code == 200
        assert voicebox.last_generate()["profile_id"] != "p-stale"
        created = [s for s in voicebox.seen
                   if s["path"] == "/profiles" and s["method"] == "POST"]
        assert len(created) == 1, "a fresh, drivable preset should have been made"

    def test_a_freshly_created_profile_is_checked_too(self, voicebox, client):
        """The one row that never passes through the usable filter is the one
        Voicebox hands back from POST /profiles. If it comes back as something
        Apex cannot drive, that has to be refused rather than spoken with."""
        voicebox.profiles = [p for p in voicebox.profiles if p["id"] != "p-ryan"]
        voicebox.created_override = {"id": "p-bad", "name": "Apex Qwen Local",
                                     "language": "en", "voice_type": "import"}
        r = speak(client, profile="")
        assert r.status_code == 503
        assert not r.headers["content-type"].startswith("audio")

    def test_an_unknown_voice_type_keeps_working(self, voicebox, client):
        """Deliberate, and the one place this does NOT follow the codebase's
        deny-by-default doctrine. Voicebox owns this vocabulary and its source
        is not readable from this machine; hard-failing an unrecognised type
        would break a working voice the first time Voicebox ships a new kind,
        and there is no safety stake here to justify that. What changed is that
        it is now one decision in one place instead of an accident of an
        `else` branch."""
        r = speak(client, profile="p-future")
        assert r.status_code == 200
        assert voicebox.last_generate()["engine"] == "qwen"

    def test_a_profile_with_no_voice_type_at_all_still_works(self, voicebox, client):
        """tests/test_voicebox.py's own fixtures omit the field entirely."""
        voicebox.profiles.append({"id": "p-bare", "name": "Bare", "language": "en"})
        assert speak(client, profile="p-bare").status_code == 200

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


class TestThePickerSaysWhatIsActuallyWrong:
    """/api/voicebox/profiles used to answer every failure — including bugs
    in Apex — with "Keep Voicebox open on the Apex laptop.", sending the user
    to restart a program that was already running."""

    def test_voicebox_not_running_says_keep_it_open(self, monkeypatch, client):
        import config
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here
        monkeypatch.setattr(config, "VOICEBOX_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "t0ken")
        r = client.get("/api/voicebox/profiles", headers=AUTH)
        assert r.status_code == 503
        assert "Keep Voicebox open" in r.json()["error"]

    def test_voicebox_answering_badly_is_not_told_to_stay_open(self, voicebox, client):
        voicebox.profiles = {"not": "a list"}
        r = client.get("/api/voicebox/profiles", headers=AUTH)
        assert r.status_code == 503
        assert "Update Voicebox" in r.json()["error"]
        assert "Keep Voicebox open" not in r.json()["error"]

    def test_an_apex_bug_is_a_500_not_advice(self, monkeypatch, voicebox):
        from voice import voicebox as vb

        async def broken():
            raise KeyError("a bug in Apex")
        monkeypatch.setattr(vb, "profiles", broken)
        from dashboard.server import app
        r = TestClient(app, raise_server_exceptions=False).get(
            "/api/voicebox/profiles", headers=AUTH)
        assert r.status_code == 500
