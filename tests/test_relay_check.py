"""`python -m agent.relay --check` — the instrument that makes step 9 finishable.

Step 9 of docs/PHASE_6_7_PLAN.md is "deploy it and use it", and it is the only
step nobody can do from here. The thing that was missing was not code: it was
any way to tell, after setting a relay up, whether it worked. `status()` reports
what THIS PROCESS has done, so on a freshly opened shell it says "never_pushed"
whether the relay is perfect or unplugged — the wrong instrument for the only
question being asked.

Two of these tests exist because the first version of `check()` got them wrong,
and both mistakes passed a run against a working relay:

  * it called `pull_snapshot()` to inspect what the relay stores. That function
    unseals for you, so the ciphertext test was asking the question of a value
    that had already been decrypted — and reported a correctly-sealed relay as
    storing plaintext.
  * it pushed BEFORE verifying, so "can my key open what is stored" compared the
    current key against a snapshot it had just sealed with that same key. A
    stale RELAY_KEY — a laptop restored from backup, the realistic failure —
    was reported as healthy.
"""
from __future__ import annotations

import importlib.util
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "relay" / "server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("relay_server_check", SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay_box(tmp_path):
    """A real relay on a real port, exactly as it is deployed."""
    mod = _load_server()
    mod.TOKEN = "token-for-the-box"
    port = _free_port()
    srv = mod.serve(host="127.0.0.1", port=port, db_path=str(tmp_path / "box.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", "token-for-the-box", mod
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def laptop(tmp_path, monkeypatch, relay_box):
    """A laptop configured to talk to it, with its own memory database."""
    import config
    from agent import longterm, relay
    url, token, _ = relay_box
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "laptop.db"))
    longterm.init_db()
    longterm.remember("A fact only the laptop should be able to read.", kind="fact")
    monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RELAY_URL", url, raising=False)
    monkeypatch.setattr(config, "RELAY_TOKEN", token, raising=False)
    monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
    relay._last.update(relay._fresh_last())
    return relay


def states(results) -> dict:
    return {r["stage"]: r["state"] for r in results}


class TestAWorkingRelayPassesEveryStage:

    def test_all_stages_pass(self, laptop):
        from agent import relay
        result = states(laptop.check())
        assert result["configured"] == relay.CHECK_OK
        assert result["relay refuses strangers"] == relay.CHECK_OK
        assert result["snapshot uploads"] == relay.CHECK_OK
        assert result["relay cannot read it"] == relay.CHECK_OK
        assert result["your key opens it"] == relay.CHECK_OK

    def test_a_first_run_skips_rather_than_failing(self, laptop):
        """Nothing is stored yet. That is not a fault, and reporting it as one
        would teach the user to ignore the first run's output."""
        from agent import relay
        assert states(laptop.check())["the snapshot already there opens"] == \
            relay.CHECK_SKIP

    def test_the_report_exits_clean(self, laptop):
        assert "FAIL" not in laptop.check_report()


class TestItLooksAtWhatTheRelayActuallyHolds:
    """The ciphertext stage has to read the STORED bytes. `pull_snapshot`
    unseals, so asking it whether the relay holds ciphertext returns the
    comfortable answer every time."""

    def test_the_stored_bytes_are_ciphertext_not_a_database(self, laptop):
        from agent import relay
        laptop.push_snapshot()
        raw = relay._http("GET", "/snapshot")
        assert raw[:15] != b"SQLite format 3"
        assert relay.unseal(raw)[:15] == b"SQLite format 3"

    def test_the_check_does_not_route_through_the_unsealing_helper(self, laptop, monkeypatch):
        """If it did, it would be inspecting plaintext and calling it proof."""
        from agent import relay
        monkeypatch.setattr(relay, "pull_snapshot", lambda: (_ for _ in ()).throw(
            AssertionError("check() must read the stored bytes, not the unsealed ones")))
        assert states(laptop.check())["relay cannot read it"] == relay.CHECK_OK


class TestAStaleKeyIsCaught:
    """A laptop restored from a backup, or a key regenerated by accident. The
    snapshots already on the relay are then unreadable forever, and that is
    worth being told immediately rather than discovering when you need them."""

    def test_a_key_that_cannot_open_the_stored_snapshot_fails(self, laptop, monkeypatch):
        import config
        from agent import relay
        laptop.push_snapshot()                       # sealed with the real key
        monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
        result = states(laptop.check())
        assert result["the snapshot already there opens"] == relay.CHECK_FAIL

    def test_the_remedy_says_it_is_unrecoverable(self, laptop, monkeypatch):
        import config
        from agent import relay
        laptop.push_snapshot()
        monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
        fix = [r["fix"] for r in laptop.check()
               if r["stage"] == "the snapshot already there opens"][0]
        assert "nothing can recover it" in fix

    def test_the_check_runs_before_anything_is_pushed(self, laptop, monkeypatch):
        """Ordering IS the test. Push first and the stage compares the current
        key with a snapshot it just sealed using that key."""
        import config
        from agent import relay
        laptop.push_snapshot()
        monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
        report = laptop.check_report()
        assert "FAIL" in report


class TestEachMisconfigurationIsNamed:

    def test_disabled(self, laptop, monkeypatch):
        import config
        from agent import relay
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
        assert states(laptop.check())["configured"] == relay.CHECK_FAIL

    def test_no_url(self, laptop, monkeypatch):
        import config
        from agent import relay
        monkeypatch.setattr(config, "RELAY_URL", "", raising=False)
        assert states(laptop.check())["configured"] == relay.CHECK_FAIL

    def test_no_token(self, laptop, monkeypatch):
        import config
        from agent import relay
        monkeypatch.setattr(config, "RELAY_TOKEN", "", raising=False)
        assert states(laptop.check())["configured"] == relay.CHECK_FAIL

    def test_an_unusable_key_is_caught_before_the_network(self, laptop, monkeypatch):
        import config
        from agent import relay
        monkeypatch.setattr(config, "RELAY_KEY", "not-a-real-fernet-key", raising=False)
        result = laptop.check()
        assert states(result)["configured"] == relay.CHECK_FAIL
        assert "--new-key" in result[0]["fix"]

    def test_an_unreachable_relay_says_so_and_stops(self, laptop, monkeypatch):
        import config
        from agent import relay
        monkeypatch.setattr(config, "RELAY_URL", f"http://127.0.0.1:{_free_port()}",
                            raising=False)
        result = states(laptop.check())
        assert result["relay reachable"] == relay.CHECK_FAIL
        assert "snapshot uploads" not in result, "it should not keep going"

    def test_a_relay_serving_without_a_token_is_a_failure(self, laptop, monkeypatch):
        """The inversion this project has already shipped once, on /board: a
        missing credential waving everyone through instead of refusing.

        Pointed at a DELIBERATELY PERMISSIVE server rather than at ours with
        the token blanked — blanking `relay/server.py`'s TOKEN makes it refuse
        everything, which is the property it promises, so that version of this
        test proved only that our relay is not the thing being guarded against.
        The stage exists for a relay somebody else deployed, or ours started
        wrong, and that is what is stood up here.
        """
        import config
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from agent import relay

        class Open(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"hi")

        port = _free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", port), Open)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(config, "RELAY_URL", f"http://127.0.0.1:{port}",
                                raising=False)
            result = laptop.check()
            assert states(result)["relay refuses strangers"] == relay.CHECK_FAIL
            fix = [r["fix"] for r in result
                   if r["stage"] == "relay refuses strangers"][0]
            assert "RELAY_SERVER_TOKEN" in fix
        finally:
            srv.shutdown()
            srv.server_close()
