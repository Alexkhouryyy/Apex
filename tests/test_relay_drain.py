"""The outbox drain, end to end against a real relay on a real socket.

Step 3 of docs/PHASE_6_7_PLAN.md. Nothing here is faked except the passage of
time: an item is sealed on the laptop, POSTed over HTTP to `relay/server.py`,
stored in its SQLite, fetched back, unsealed and applied to Apex's memory. A
fake transport would have proved the loop and skipped every place the two
halves actually have to agree.

The success check is "a message queued while offline is applied exactly once",
and two qualifications are load-bearing:

  * Dedupe keys on the item's OWN id, not the relay's row id. A relay operator
    cannot forge an item — no key — but can replay one they already store, and
    a replay under a fresh row id would otherwise be a second legitimate-looking
    delivery. `TestAReplayIsNotASecondDelivery` is that.
  * Exactly-once cannot be guaranteed across two systems with no shared
    transaction. What is guaranteed is that a crash between claim and apply is
    RETRIED rather than abandoned, bounded and recorded — because a duplicated
    note is visible and harmless and a dropped one is neither.
"""
import importlib.util
import socket
import threading
from pathlib import Path

import pytest

import config
from agent import longterm, relay

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A real relay, a real laptop database, and Apex pointed at both."""
    spec = importlib.util.spec_from_file_location(
        "relay_server", ROOT / "relay" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TOKEN = "tok"
    port = _free_port()
    srv = mod.serve(host="127.0.0.1", port=port,
                    db_path=str(tmp_path / "relay.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "brain.db"))
    longterm.init_db()
    relay.init_db()

    monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RELAY_URL", f"http://127.0.0.1:{port}", raising=False)
    monkeypatch.setattr(config, "RELAY_TOKEN", "tok", raising=False)
    monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
    try:
        yield mod, srv
    finally:
        srv.shutdown()
        srv.server_close()


def _memories():
    with longterm._conn() as c:
        return [r[0] for r in c.execute("SELECT content FROM memories ORDER BY id")]


def _applied_rows():
    with longterm._conn() as c:
        return c.execute("SELECT item_id, status, attempts, error "
                         "FROM relay_applied ORDER BY rowid").fetchall()


class TestTheHappyPath:
    def test_a_queued_note_reaches_memory(self, live):
        """Queued 'while offline', drained 'on return' — through a real socket,
        a real SQLite on the relay, real sealing at both ends."""
        assert relay.queue("note", text="call the dentist")["ok"] is True
        assert _memories() == []                       # not applied by queueing
        out = relay.drain()
        assert out["applied"] == 1
        assert _memories() == ["call the dentist"]

    def test_the_relay_stops_offering_it_afterwards(self, live):
        relay.queue("note", text="one")
        relay.drain()
        assert relay.pending() == []

    def test_several_items_arrive_in_order(self, live):
        for t in ("first", "second", "third"):
            relay.queue("note", text=t)
        relay.drain()
        assert _memories() == ["first", "second", "third"]

    def test_nothing_queued_is_not_an_error(self, live):
        out = relay.drain()
        assert out["ok"] is True and out["applied"] == 0


class TestExactlyOnce:
    def test_draining_twice_applies_once(self, live):
        relay.queue("note", text="call the dentist")
        relay.drain()
        relay.drain()
        assert _memories() == ["call the dentist"]

    def test_an_item_the_relay_never_stopped_offering_is_not_reapplied(self, live):
        """The acknowledgement is the fragile half: if the POST that marks an
        item done never lands, the relay keeps offering it forever. Dedupe, not
        the ack, is what makes that safe."""
        relay.queue("note", text="only once")
        monkey = []
        real_http = relay._http

        def no_ack(method, path, body=None, timeout=30.0):
            if path.endswith("/done"):
                monkey.append(path)
                raise relay.RelayError("ack lost in transit")
            return real_http(method, path, body, timeout)

        relay._http = no_ack
        try:
            relay.drain()
            assert monkey, "the test did not actually intercept an ack"
            assert relay.pending(), "the relay should still be offering it"
        finally:
            relay._http = real_http
        relay.drain()
        assert _memories() == ["only once"]

    def test_a_lost_ack_is_retried_on_the_next_drain(self, live):
        relay.queue("note", text="x")
        real_http = relay._http

        def no_ack(method, path, body=None, timeout=30.0):
            if path.endswith("/done"):
                raise relay.RelayError("ack lost")
            return real_http(method, path, body, timeout)

        relay._http = no_ack
        try:
            relay.drain()
        finally:
            relay._http = real_http
        out = relay.drain()
        assert out["skipped"] == 1
        assert relay.pending() == [], "the second drain should have acked it"


class TestAReplayIsNotASecondDelivery:
    def test_the_same_item_posted_twice_applies_once(self, live):
        """The relay operator holds sealed items and can put one back. They
        cannot forge one — no key — so the id inside is the thing they cannot
        choose, and dedupe keys on that rather than on the row id they can."""
        import json
        item = relay.new_item("note", text="transfer the money")
        blob = relay.seal(json.dumps(item).encode())
        relay._http("POST", "/outbox", blob)
        relay.drain()
        assert _memories() == ["transfer the money"]

        relay._http("POST", "/outbox", blob)          # replayed, new row id
        out = relay.drain()
        assert out["applied"] == 0 and out["skipped"] == 1
        assert _memories() == ["transfer the money"], \
            "a replayed item was applied a second time"

    def test_keying_on_the_relays_row_id_would_not_have_caught_it(self, live):
        """States why the id has to come from inside. The two POSTs above get
        different row ids from the relay, so a dedupe table keyed on those would
        see two different items."""
        import json
        item = relay.new_item("note", text="y")
        blob = relay.seal(json.dumps(item).encode())
        relay._http("POST", "/outbox", blob)
        relay._http("POST", "/outbox", blob)
        ids = {p["relay_id"] for p in relay.pending()}
        assert len(ids) == 2, "the relay assigns a fresh row id to a replay"
        inner = {p["id"] for p in relay.pending()}
        assert len(inner) == 1, "the id inside the seal is the stable one"


class TestUnknownKindsAreRefusedNotGuessed:
    def test_an_unsupported_kind_is_not_applied(self, live):
        """Deny-by-default, the same posture as ROLE_TOOLS and MCP_ALLOW. An
        older laptop meeting a kind a newer phone invented must refuse it."""
        relay.queue("shell", command="rm -rf /")
        out = relay.drain()
        assert out["applied"] == 0 and out["failed"] == 1
        assert _memories() == []
        rows = _applied_rows()
        assert rows[0][1] == "unsupported" and "shell" in rows[0][3]

    def test_it_is_recorded_rather_than_dropped(self, live):
        relay.queue("teleport", to="mars")
        relay.drain()
        assert len(_applied_rows()) == 1, \
            "a refused item must leave a trace, or it is indistinguishable " \
            "from one that never arrived"

    def test_an_item_with_no_id_is_refused(self, live):
        """Without an id there is no way to avoid applying it again, so it is
        never applied at all."""
        import json
        relay._http("POST", "/outbox",
                    relay.seal(json.dumps({"kind": "note",
                                           "payload": {"text": "no id"}}).encode()))
        out = relay.drain()
        assert out["failed"] == 1 and _memories() == []


class TestFailuresAndRetries:
    def test_a_handler_that_raises_is_recorded_and_not_acked(self, live):
        relay.queue("note", text="")          # _apply_note refuses empty text
        out = relay.drain()
        assert out["failed"] == 1 and _memories() == []
        assert _applied_rows()[0][1] == "failed"

    def test_an_interrupted_apply_is_retried_not_abandoned(self, live):
        """The crash window. A row left in_progress by a process that died is
        re-claimed on the next drain, because a duplicated note is visible and
        harmless while a dropped one is neither."""
        relay.queue("note", text="survived the crash")
        # Exactly the state a crash between _claim and _finish leaves behind.
        item_id = relay.pending()[0]["id"]
        with longterm._conn() as c:
            c.execute("INSERT INTO relay_applied (item_id, relay_id, kind, "
                      "status, attempts, first_seen) VALUES (?, 0, 'note', "
                      "'in_progress', 1, 0)", (item_id,))
            c.commit()
        out = relay.drain()
        assert out["applied"] == 1
        assert _memories() == ["survived the crash"]

    def test_retrying_stops_at_the_limit(self, live):
        """A retry that never gives up is an infinite loop wearing a feature's
        clothes."""
        relay.queue("note", text="stuck")
        item_id = relay.pending()[0]["id"]
        with longterm._conn() as c:
            c.execute("INSERT INTO relay_applied (item_id, relay_id, kind, "
                      "status, attempts, first_seen) VALUES (?, 0, 'note', "
                      "'in_progress', ?, 0)",
                      (item_id, relay.APPLY_MAX_ATTEMPTS))
            c.commit()
        out = relay.drain()
        assert out["applied"] == 0 and out["skipped"] == 1

    def test_an_unreadable_item_is_reported_not_silently_skipped(self, live):
        """A key change or a tampered store must not read as "you have no
        messages"."""
        relay._http("POST", "/outbox", b"this is not a sealed anything")
        out = relay.drain()
        assert out["failed"] == 1
        assert any(p.get("unreadable") for p in relay.pending())

    def test_one_bad_item_does_not_stop_the_rest(self, live):
        relay._http("POST", "/outbox", b"garbage")
        relay.queue("note", text="still arrives")
        out = relay.drain()
        assert out["applied"] == 1 and out["failed"] == 1
        assert _memories() == ["still arrives"]


class TestQueueingRespectsTheOffSwitch:
    def test_queueing_while_disabled_sends_nothing(self, live, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
        assert relay.queue("note", text="x")["ok"] is False
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        assert relay.pending() == []

    def test_draining_while_disabled_does_nothing(self, live, monkeypatch):
        relay.queue("note", text="x")
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
        assert relay.drain()["ok"] is False
        assert _memories() == []


class TestStartingUpSaysWhatItDecided:
    """A subsystem that is configured, constructed and silently never runs is
    indistinguishable from one that works. "Off" has to be as loud as "on"."""

    def _off(self, monkeypatch):
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "", raising=False)
        monkeypatch.setattr(config, "RELAY_KEY", "", raising=False)
        monkeypatch.setattr(relay, "_thread", None)

    def test_disabled_says_so(self, monkeypatch):
        self._off(monkeypatch)
        assert "Off" in relay.start_background()

    def test_enabled_without_a_url_says_so(self, monkeypatch):
        self._off(monkeypatch)
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        assert "RELAY_URL is empty" in relay.start_background()

    def test_a_bad_key_stops_it_at_boot_not_at_the_first_push(self, monkeypatch):
        """Finding out the key is wrong half an hour later, in a thread, is how
        a relay ends up quietly never having sent anything."""
        self._off(monkeypatch)
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "http://x", raising=False)
        monkeypatch.setattr(config, "RELAY_KEY", "hunter2", raising=False)
        line = relay.start_background()
        assert "NOT started" in line and "--new-key" in line

    def test_a_good_config_starts_and_says_where(self, monkeypatch):
        """The loop body is stubbed, deliberately. `start_background` drains
        before its first sleep — correct behaviour, since work waiting from
        while the laptop was off is the thing someone is actually waiting on —
        but that means a real thread would outlive this test and run against
        whatever config monkeypatch has since restored. What is under test here
        is the decision and the line it prints, and the loop's contents have
        their own tests above."""
        self._off(monkeypatch)
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "http://x", raising=False)
        monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
        ran = []
        monkeypatch.setattr(relay, "drain", lambda: ran.append("drain"))
        monkeypatch.setattr(relay, "push_snapshot", lambda: ran.append("push"))
        try:
            line = relay.start_background()
            assert "http://x" in line and "Watching" in line
            relay._thread.join(timeout=2)
        finally:
            relay.stop_background()
            if relay._thread is not None:
                relay._thread.join(timeout=2)
        assert ran[:2] == ["drain", "push"], (
            "the loop must drain before it sleeps — otherwise work that arrived "
            "while the laptop was off waits another full interval")
