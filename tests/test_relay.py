"""Sealing and snapshotting for the always-on relay.

Step 1 of docs/PHASE_6_7_PLAN.md. The relay is a machine we do not trust to
read what it stores, so the load-bearing tests here are not "does encryption
work" — that is `cryptography`'s job — but "can Apex ever be persuaded to send
something readable", which is Apex's.

`TestNothingLeavesWithoutAKey` is the one that matters. A misconfigured .env
pushing a plaintext copy of every memory to a rented machine would look
identical from the dashboard, the logs and the phone: there is no observation
from outside that distinguishes a sealed push from an unsealed one. The refusal
has to happen at the only place that can tell, and these tests are what hold it
there.
"""
import sqlite3

import pytest

import config
from agent import relay


@pytest.fixture
def key():
    return relay.new_key()


@pytest.fixture
def relay_off(monkeypatch):
    """Known config. A developer's own .env must not decide these outcomes."""
    monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "RELAY_URL", "", raising=False)
    monkeypatch.setattr(config, "RELAY_TOKEN", "", raising=False)
    monkeypatch.setattr(config, "RELAY_KEY", "", raising=False)
    monkeypatch.setattr(relay, "_last",
                        {"pushed_at": 0.0, "bytes": 0, "error": "",
                         "attempted_at": 0.0})


class TestNothingLeavesWithoutAKey:
    def test_no_key_refuses(self, relay_off):
        with pytest.raises(relay.RelayError) as e:
            relay.seal(b"the dentist appointment")
        assert "RELAY_KEY" in str(e.value)

    def test_a_blank_key_is_not_a_key(self, relay_off):
        with pytest.raises(relay.RelayError):
            relay.seal(b"x", "   ")

    def test_a_passphrase_is_refused_rather_than_stretched(self, relay_off):
        """Deriving a key from `hunter2` is defensible done with a salt and a
        proper KDF, and indefensible done casually — and the casual version
        looks exactly like the careful one from outside. So: refuse, and say
        how to make a real one."""
        with pytest.raises(relay.RelayError) as e:
            relay.seal(b"x", "hunter2")
        assert "--new-key" in str(e.value)

    def test_it_never_returns_the_plaintext_as_a_fallback(self, relay_off):
        """The failure mode this whole class exists for: a `seal` that shrugged
        and returned its input would satisfy every caller and every log line."""
        for bad in ("", "   ", "hunter2", "not-base64!!!"):
            with pytest.raises(relay.RelayError):
                out = relay.seal(b"secret", bad)
                assert out != b"secret"   # unreachable; states the claim anyway


class TestSealing:
    def test_the_plaintext_is_not_in_the_ciphertext(self, key):
        out = relay.seal(b"my bank password is swordfish", key)
        assert b"swordfish" not in out
        assert b"password" not in out

    def test_it_round_trips(self, key):
        assert relay.unseal(relay.seal(b"hello", key), key) == b"hello"

    def test_a_different_key_cannot_open_it(self, key):
        sealed = relay.seal(b"hello", key)
        with pytest.raises(relay.RelayError):
            relay.unseal(sealed, relay.new_key())

    def test_a_tampered_payload_fails_to_open_rather_than_opening_as_something_else(
            self, key):
        """Why authenticated encryption, not just encryption. The relay is a
        machine we do not trust to read the snapshot, so it is also one we
        should not trust to hand back what it was given."""
        sealed = bytearray(relay.seal(b"transfer 100", key))
        sealed[-6] ^= 0x01
        with pytest.raises(relay.RelayError) as e:
            relay.unseal(bytes(sealed), key)
        assert "did not decrypt" in str(e.value)

    def test_truncation_fails_too(self, key):
        with pytest.raises(relay.RelayError):
            relay.unseal(relay.seal(b"hello", key)[:-10], key)

    def test_the_same_input_seals_differently_each_time(self, key):
        """Fernet includes a random IV. Identical ciphertexts would tell the
        relay operator when your memory did not change, which is information
        they are not supposed to have."""
        assert relay.seal(b"same", key) != relay.seal(b"same", key)

    def test_it_refuses_a_str(self, key):
        with pytest.raises(relay.RelayError):
            relay.seal("bytes please", key)


class TestSnapshots:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from agent import longterm
        path = tmp_path / "brain.db"
        monkeypatch.setattr(longterm, "DB_PATH", str(path))
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
            c.execute("INSERT INTO memories (content) VALUES ('call the dentist')")
            c.commit()
        return path

    def test_it_captures_the_rows(self, db, tmp_path):
        blob = relay.snapshot_bytes()
        out = tmp_path / "restored.db"
        out.write_bytes(blob)
        with sqlite3.connect(out) as c:
            rows = c.execute("SELECT content FROM memories").fetchall()
        assert rows == [("call the dentist",)]

    def test_it_reads_uncommitted_wal_writes_a_file_copy_would_miss(self, db, tmp_path):
        """The reason this uses SQLite's backup API rather than reading the
        file. In WAL mode the newest writes live in a -wal sidecar; copying the
        .db alone silently drops them, and a snapshot missing your last hour is
        worse than no snapshot because it looks complete."""
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO memories (content) VALUES ('the newest thing')")
        conn.commit()
        try:
            blob = relay.snapshot_bytes()
        finally:
            conn.close()
        out = tmp_path / "restored.db"
        out.write_bytes(blob)
        with sqlite3.connect(out) as c:
            got = {r[0] for r in c.execute("SELECT content FROM memories")}
        assert "the newest thing" in got

    def test_a_missing_database_says_so(self, tmp_path, monkeypatch):
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "nope.db"))
        with pytest.raises(relay.RelayError) as e:
            relay.snapshot_bytes()
        assert "no database" in str(e.value)

    def test_the_snapshot_that_goes_up_is_unreadable(self, db, key):
        """The end-to-end claim, asserted on the actual bytes that would cross
        the wire rather than on the function that produces them."""
        blob = relay.seal(relay.snapshot_bytes(), key)
        assert b"call the dentist" not in blob
        assert b"SQLite format" not in blob
        assert b"call the dentist" in relay.unseal(blob, key)


class TestPushing:
    @pytest.fixture
    def on(self, tmp_path, monkeypatch, key):
        from agent import longterm
        path = tmp_path / "brain.db"
        monkeypatch.setattr(longterm, "DB_PATH", str(path))
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
            c.execute("INSERT INTO memories (content) VALUES ('call the dentist')")
            c.commit()
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "https://relay.example", raising=False)
        monkeypatch.setattr(config, "RELAY_TOKEN", "tok", raising=False)
        monkeypatch.setattr(config, "RELAY_KEY", key, raising=False)
        monkeypatch.setattr(relay, "_last",
                            {"pushed_at": 0.0, "bytes": 0, "error": "",
                             "attempted_at": 0.0})
        return key

    def test_what_reaches_the_wire_is_sealed(self, on, monkeypatch):
        """The single most important test in this file: it inspects the bytes
        the transport was handed, not the return value of seal()."""
        sent = {}
        monkeypatch.setattr(relay, "_http",
                            lambda m, p, body=None, timeout=30.0:
                                sent.update(method=m, path=p, body=body) or b"")
        assert relay.push_snapshot()["ok"] is True
        assert sent["method"] == "PUT" and sent["path"] == "/snapshot"
        assert b"call the dentist" not in sent["body"]
        assert relay.unseal(sent["body"], on).find(b"call the dentist") > 0

    def test_the_key_is_never_sent(self, on, monkeypatch):
        sent = {}
        monkeypatch.setattr(relay, "_http",
                            lambda m, p, body=None, timeout=30.0:
                                sent.update(body=body) or b"")
        relay.push_snapshot()
        assert on.encode() not in sent["body"]

    def test_disabled_sends_nothing_and_says_why(self, on, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)

        def _must_not_run(*a, **k):
            raise AssertionError("the transport ran while the relay was off")
        monkeypatch.setattr(relay, "_http", _must_not_run)
        out = relay.push_snapshot()
        assert out["ok"] is False and "RELAY_ENABLED" in out["skipped"]

    def test_a_missing_key_stops_the_push_rather_than_sending_plaintext(
            self, on, monkeypatch):
        monkeypatch.setattr(config, "RELAY_KEY", "", raising=False)

        def _must_not_run(*a, **k):
            raise AssertionError("a push was attempted with no key")
        monkeypatch.setattr(relay, "_http", _must_not_run)
        out = relay.push_snapshot()
        assert out["ok"] is False and "RELAY_KEY" in out["error"]

    def test_an_unreachable_relay_is_recorded_not_raised(self, on, monkeypatch):
        """A relay down for a week is not an error anyone sees at the moment it
        happens — it is a stale snapshot the phone reads later and believes."""
        def _boom(*a, **k):
            raise relay.RelayError("could not reach the relay")
        monkeypatch.setattr(relay, "_http", _boom)
        out = relay.push_snapshot()
        assert out["ok"] is False
        assert relay.status()["state"] == "failing"

    def test_pull_round_trips_through_the_transport(self, on, monkeypatch):
        store = {}
        def _fake(m, p, body=None, timeout=30.0):
            if m == "PUT":
                store["blob"] = body
                return b""
            return store["blob"]
        monkeypatch.setattr(relay, "_http", _fake)
        relay.push_snapshot()
        assert b"call the dentist" in relay.pull_snapshot()


class TestStatusTellsFourStatesApart:
    """One boolean would flatten these into "no relay", and they need four
    different fixes — the reasoning mcp_client.status already uses."""

    def test_off(self, relay_off):
        assert relay.status()["state"] == "off"

    def test_enabled_but_unconfigured(self, relay_off, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        assert relay.status()["state"] == "unconfigured"

    def test_configured_but_never_pushed(self, relay_off, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "https://relay.example", raising=False)
        assert relay.status()["state"] == "never_pushed"

    def test_failing_says_how_old_the_readable_copy_is(self, relay_off, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "https://relay.example", raising=False)
        monkeypatch.setattr(relay, "_last",
                            {"pushed_at": 0.0, "bytes": 0,
                             "error": "could not reach the relay",
                             "attempted_at": 1.0})
        s = relay.status()
        assert s["state"] == "failing" and "never" in s["detail"]


class TestTheTransportRefusesToBeAnonymous:
    def test_no_url(self, relay_off):
        with pytest.raises(relay.RelayError) as e:
            relay._http("GET", "/snapshot")
        assert "RELAY_URL" in str(e.value)

    def test_no_token(self, relay_off, monkeypatch):
        """An unauthenticated relay would accept a snapshot from anyone who
        found the URL, and hand yours to them."""
        monkeypatch.setattr(config, "RELAY_URL", "https://relay.example", raising=False)
        with pytest.raises(relay.RelayError) as e:
            relay._http("GET", "/snapshot")
        assert "RELAY_TOKEN" in str(e.value)
