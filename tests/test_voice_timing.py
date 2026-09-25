"""Pillar 1's measurement: where the time goes in a voice turn."""
from __future__ import annotations

import pytest

from agent import voice_timing as vt


@pytest.fixture
def db(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "t.db"))
    longterm.init_db()


def turn(first_sound, **extra):
    stages = {"stt_done": 400, "first_token": 900, "reply_done": 2000,
              "tts_start": 2010, "tts_ready": first_sound - 50,
              "first_sound": first_sound, **extra}
    return {"mode": "tap", "voice": "voicebox", "stages": stages}


class TestRecording:

    def test_a_turn_is_stored_and_read_back(self, db):
        vt.record(turn(3000, stt_server=350))
        (row,) = vt.recent()
        assert row["mode"] == "tap" and row["stages"]["first_sound"] == 3000
        assert row["stages"]["stt_server"] == 350

    @pytest.mark.parametrize("bad", [None, [], {"mode": "tap"},
                                     {"mode": "shout", "stages": {"first_sound": 1}},
                                     {"mode": "tap", "stages": {}},
                                     {"mode": "tap", "stages": {"first_sound": -5}}])
    def test_things_that_are_not_turns_are_refused(self, db, bad):
        with pytest.raises(ValueError):
            vt.record(bad)

    def test_junk_stages_are_dropped_not_stored(self, db):
        stored = vt.record({"mode": "tap", "stages": {
            "first_sound": 1200, "evil": 5, "stt_done": True,
            "first_token": float("nan"), "reply_done": 10 ** 12}})
        assert stored == {"first_sound": 1200}

    def test_old_turns_are_trimmed(self, db, monkeypatch):
        monkeypatch.setattr(vt, "KEEP", 5)
        for i in range(8):
            vt.record(turn(1000 + i))
        assert len(vt.recent(100)) == 5


class TestTheVerdict:
    """Three-state, like everything else here: too few turns is `unknown`,
    never `pass`."""

    def test_too_few_turns_is_unknown_not_pass(self, db):
        for _ in range(5):
            vt.record(turn(900))                     # fast, but only five
        s = vt.summary()
        assert s["verdict"] == "unknown"

    def test_twenty_fast_turns_pass(self, db):
        for i in range(20):
            vt.record(turn(1000 + i * 10))
        s = vt.summary()
        assert s["verdict"] == "pass"
        assert s["stages"]["first_sound"]["median"] <= vt.TARGET_MEDIAN_MS

    def test_a_slow_tail_fails_even_with_a_fast_median(self, db):
        """Median alone would call this fast. Three turns in twenty taking
        eight seconds is what "it's slow sometimes" feels like."""
        for i in range(17):
            vt.record(turn(1000))
        for i in range(3):
            vt.record(turn(8000))
        s = vt.summary()
        assert s["stages"]["first_sound"]["median"] <= vt.TARGET_MEDIAN_MS
        assert s["verdict"] == "fail"

    def test_minutes_fail(self, db):
        for _ in range(20):
            vt.record(turn(120_000))
        assert vt.summary()["verdict"] == "fail"

    def test_the_report_names_every_stage_and_the_verdict(self, db):
        for _ in range(3):
            vt.record(turn(4000, stt_server=300, tts_server=2500))
        text = vt.report()
        for label in ("transcript back", "first word of reply", "whole reply written",
                      "FIRST SOUND", "server: speech-to-text"):
            assert label in text
        assert "UNKNOWN" in text


class TestTheEndpoints:

    @pytest.fixture
    def client(self, db, monkeypatch):
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        return TestClient(server.app)

    def test_post_then_summary(self, client):
        r = client.post("/api/companion/timing", json=turn(2500))
        assert r.status_code == 200
        body = client.get("/api/companion/timing").json()
        assert body["summary"]["turns"] == 1
        assert body["turns"][0]["stages"]["first_sound"] == 2500

    def test_garbage_is_a_400(self, client):
        assert client.post("/api/companion/timing", content=b"{nope").status_code == 400
        assert client.post("/api/companion/timing", json={"mode": "x"}).status_code == 400

    def test_transcription_reports_its_server_time(self, client, monkeypatch):
        import voice.browser_stt as stt
        monkeypatch.setattr(stt, "transcribe", lambda data, name, engine: "hello")
        r = client.post("/api/companion/transcribe?engine=local", content=b"audio",
                        headers={"Content-Type": "audio/webm"})
        assert r.status_code == 200 and r.json() == {"text": "hello"}
        assert r.headers["Server-Timing"].startswith("stt;dur=")

    def test_the_voice_reports_its_synthesis_time(self, client, monkeypatch):
        import voice.voicebox as vb

        async def fake(text, profile=""):
            return b"RIFFwav"
        monkeypatch.setattr(vb, "synthesize", fake)
        r = client.post("/api/speak", json={"text": "Hi.", "engine": "voicebox"})
        assert r.status_code == 200
        assert r.headers["Server-Timing"].startswith("tts;dur=")


def test_the_table_is_created_at_boot():
    from agent import schema
    assert "voice_timing" in schema.INIT_MODULES


class TestBeforeAndAfter:
    """"Speak as it writes" is judged by measurement: each turn records which
    mode it used, and the report puts the two side by side."""

    def _turn(self, first_sound, streamed):
        t = turn(first_sound)
        t["streamed"] = streamed
        return t

    def test_the_mode_is_stored_and_filtered(self, db):
        vt.record(self._turn(9000, False))
        vt.record(self._turn(3000, True))
        vt.record(turn(5000))                          # an old client: unknown
        assert [t["streamed"] for t in vt.recent()] == [None, True, False]
        assert [t["stages"]["first_sound"] for t in vt.recent(streamed=True)] == [3000]
        assert [t["stages"]["first_sound"] for t in vt.recent(streamed=False)] == [9000]

    def test_only_a_real_boolean_counts(self, db):
        vt.record({"mode": "tap", "streamed": "yes", "stages": {"first_sound": 1}})
        assert vt.recent()[0]["streamed"] is None

    def test_the_report_compares_the_two_modes(self, db):
        for _ in range(3):
            vt.record(self._turn(9000, False))
            vt.record(self._turn(3000, True))
        text = vt.report()
        assert "Before / after" in text
        assert "First sound earlier by 6.00s with it on." in text

    def test_a_regression_is_called_later_not_hidden(self, db):
        vt.record(self._turn(2000, False))
        vt.record(self._turn(5000, True))
        assert "First sound LATER by 3.00s" in vt.report()

    def test_no_comparison_until_both_modes_have_turns(self, db):
        vt.record(self._turn(3000, True))
        assert "Before / after" not in vt.report()

    def test_a_table_from_before_this_change_gains_the_column(self, db):
        from agent import longterm
        with longterm._conn() as c:
            c.execute("DROP TABLE IF EXISTS voice_timing")
            c.execute("CREATE TABLE voice_timing (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                      " ts REAL NOT NULL, mode TEXT NOT NULL, voice TEXT NOT NULL DEFAULT '',"
                      " stages TEXT NOT NULL)")
            c.execute("INSERT INTO voice_timing (ts, mode, stages) VALUES (1, 'tap',"
                      " '{\"first_sound\": 4000}')")
        vt.record(self._turn(2000, True))
        assert [t["streamed"] for t in vt.recent()] == [True, None]
