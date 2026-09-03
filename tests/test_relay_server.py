"""The relay server, driven as a real HTTP server on a real socket.

Step 2 of docs/PHASE_6_7_PLAN.md. These tests boot `relay/server.py` on a free
port and talk to it with urllib, rather than calling handler methods directly —
because the interesting failures of an HTTP server (an auth check that never
runs, a body limit that is not applied, a route that answers before the gate)
all live in the plumbing that a direct call skips.

Two classes carry the weight:

  * `TestAnUnconfiguredRelayServesNothing` — the inversion this project has
    already shipped once. Apex's dashboard middleware waved every request
    through when DASHBOARD_TOKEN was empty, which is how a real authorisation
    bug on /board stayed invisible. The same mistake here publishes a
    stranger's memory to the internet.
  * `TestItCannotReadWhatItStores` — the relay's entire security claim, checked
    structurally rather than promised in a docstring.
"""
import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "relay" / "server.py"


def _load():
    """Import relay/server.py by path — it is deliberately not a package, and
    importing it as one would quietly prove something the deployment does not
    do."""
    spec = importlib.util.spec_from_file_location("relay_server", SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """A real server on a real port, with a real token."""
    mod = _load()
    mod.TOKEN = "s3cret-token"
    port = _free_port()
    srv = mod.serve(host="127.0.0.1", port=port, db_path=str(tmp_path / "relay.db"))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield mod, f"http://127.0.0.1:{port}", "s3cret-token"
    finally:
        srv.shutdown()
        srv.server_close()


def _req(base, path, method="GET", body=None, token=None, headers=None):
    h = dict(headers or {})
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{base}{path}", data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestItCannotReadWhatItStores:
    """The relay's whole security claim. Asserted against the source, because a
    claim this load-bearing should not rest on nobody having added an import."""

    def test_it_imports_nothing_from_apex(self):
        """Every import from agent/ is a way for a change over here to quietly
        widen what runs on a machine you may not own."""
        src = SERVER_PY.read_text(encoding="utf-8")
        for forbidden in ("from agent", "import agent", "import config",
                          "from config"):
            assert forbidden not in src, (
                f"relay/server.py contains '{forbidden}'. It is meant to be one "
                f"standalone file, deployable by copying it to a box with "
                f"Python and nothing else.")

    def test_it_has_no_way_to_decrypt_anything(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        for forbidden in ("cryptography", "Fernet", "RELAY_KEY", "decrypt"):
            assert forbidden not in src, (
                f"relay/server.py mentions '{forbidden}'. There is no key here "
                f"and there must be nowhere to put one.")

    def test_it_needs_no_third_party_package(self):
        """A dependency on the relay is a supply chain attached to the machine
        holding your memory.

        Walks the AST rather than the lines. The first version of this test
        matched `line.startswith("import ")` and failed on a sentence in the
        module docstring that happened to wrap onto the word "import" — a test
        that reads prose as code will eventually pass or fail for reasons that
        have nothing to do with the imports."""
        import ast
        import sys
        stdlib = set(sys.stdlib_module_names)
        tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add((node.module or "").split(".")[0])
        assert names, "parsed no imports at all — the walk is not working"
        outside = sorted(n for n in names if n and n not in stdlib)
        assert not outside, (
            f"relay/server.py imports {outside}, which is not in the standard "
            f"library. Every dependency here is a supply chain attached to the "
            f"machine holding your memory.")

    def test_bytes_come_back_exactly_as_they_went_in(self, server):
        """Opacity in practice: a sealed blob is stored and returned byte for
        byte, with the server having no idea what any of it is."""
        mod, base, tok = server
        blob = bytes(range(256)) * 40      # every byte value, including NULs
        assert _req(base, "/snapshot", "PUT", blob, tok)[0] == 200
        code, back = _req(base, "/snapshot", token=tok)
        assert code == 200 and back == blob

    def test_a_real_sealed_snapshot_survives_the_round_trip(self, server, tmp_path):
        """End to end with Apex's own sealing, since a relay that mangles
        ciphertext by a byte would look fine until the day you needed it."""
        import sqlite3
        from agent import longterm, relay
        mod, base, tok = server
        db = tmp_path / "brain.db"
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
            c.execute("INSERT INTO memories (content) VALUES ('call the dentist')")
            c.commit()
        key = relay.new_key()
        import config
        old = getattr(longterm, "DB_PATH")
        longterm.DB_PATH = str(db)
        try:
            sealed = relay.seal(relay.snapshot_bytes(), key)
        finally:
            longterm.DB_PATH = old
        _req(base, "/snapshot", "PUT", sealed, tok)
        code, back = _req(base, "/snapshot", token=tok)
        assert code == 200
        assert b"call the dentist" not in back
        assert b"call the dentist" in relay.unseal(back, key)


class TestAnUnconfiguredRelayServesNothing:
    """`if token and token != given: deny` accepts everyone when the token is
    empty. That exact inversion shipped in Apex's dashboard middleware and hid
    a real /board authorisation bug for weeks. Here it would publish a
    stranger's memory."""

    @pytest.fixture
    def open_relay(self, tmp_path):
        mod = _load()
        mod.TOKEN = ""                     # the misconfiguration under test
        port = _free_port()
        srv = mod.serve(host="127.0.0.1", port=port,
                        db_path=str(tmp_path / "relay.db"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            srv.shutdown()
            srv.server_close()

    def test_no_token_configured_refuses_a_read(self, open_relay):
        code, body = _req(open_relay, "/snapshot")
        assert code == 401
        assert "RELAY_SERVER_TOKEN" in json.loads(body)["error"]

    def test_no_token_configured_refuses_even_a_plausible_credential(self, open_relay):
        """The failure would be accepting anything at all. Presenting a token
        must not be the thing that opens an unconfigured relay."""
        assert _req(open_relay, "/snapshot", token="")[0] == 401
        assert _req(open_relay, "/snapshot", token="anything")[0] == 401

    def test_no_token_configured_refuses_a_write(self, open_relay):
        assert _req(open_relay, "/snapshot", "PUT", b"x", "anything")[0] == 401

    def test_main_refuses_to_start_at_all(self):
        mod = _load()
        mod.TOKEN = ""
        assert mod.main() == 2, \
            "an unconfigured relay must not boot, not merely refuse requests"


class TestAuth:
    def test_no_header_is_refused(self, server):
        _, base, _ = server
        assert _req(base, "/snapshot")[0] == 401

    def test_a_wrong_token_is_refused(self, server):
        _, base, _ = server
        assert _req(base, "/snapshot", token="not-it")[0] == 401

    def test_a_token_without_the_bearer_prefix_is_refused(self, server):
        _, base, tok = server
        assert _req(base, "/snapshot", headers={"Authorization": tok})[0] == 401

    def test_the_right_token_gets_through(self, server):
        _, base, tok = server
        assert _req(base, "/snapshot", token=tok)[0] == 404   # nothing stored yet

    def test_comparison_is_constant_time(self):
        """A plain `==` returns as soon as two bytes differ, leaking the token
        one character at a time to anyone patient enough to measure.

        Reads the AST of `authorised` specifically, not the file's text. The
        first version asserted `"compare_digest" in src` and did not
        discriminate at all: swapping the real call for `!=` left the phrase
        sitting in the docstring three lines above, so the test passed by
        matching the comment that described the thing it was no longer doing.
        """
        import ast
        tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
        fn = next((n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "authorised"), None)
        assert fn is not None, "authorised() is gone — where did the auth check go?"

        calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert "hmac.compare_digest" in calls, (
            "authorised() does not call hmac.compare_digest. A plain comparison "
            "leaks the token one byte at a time under timing measurement.")

        for node in ast.walk(fn):
            if isinstance(node, ast.Compare):
                rendered = ast.unparse(node)
                assert "TOKEN" not in rendered, (
                    f"authorised() compares the token directly: {rendered}")


class TestHealth:
    def test_it_answers_without_a_credential(self, server):
        """A monitor needs to know the process is alive without holding a
        secret."""
        _, base, _ = server
        code, body = _req(base, "/health")
        assert code == 200 and json.loads(body) == {"ok": True}

    def test_it_leaks_nothing(self, server):
        """Anything more here is a fact about you served to strangers."""
        _, base, tok = server
        _req(base, "/snapshot", "PUT", b"sealed-bytes", tok)
        code, body = _req(base, "/health")
        assert set(json.loads(body)) == {"ok"}


class TestSnapshotStorage:
    def test_missing_snapshot_is_404_not_an_empty_200(self, server):
        """An empty 200 would restore as an empty brain."""
        _, base, tok = server
        assert _req(base, "/snapshot", token=tok)[0] == 404

    def test_an_empty_put_is_refused(self, server):
        """Zero bytes overwriting a good snapshot is how a backup silently
        becomes nothing."""
        _, base, tok = server
        assert _req(base, "/snapshot", "PUT", b"", tok)[0] == 400

    def test_a_second_put_replaces_the_first(self, server):
        _, base, tok = server
        _req(base, "/snapshot", "PUT", b"old", tok)
        _req(base, "/snapshot", "PUT", b"new", tok)
        assert _req(base, "/snapshot", token=tok)[1] == b"new"

    def test_only_ever_one_row(self, server, tmp_path):
        """The CHECK (id = 1) constraint, proven rather than trusted: without
        it a daily push would grow the relay's disk without bound."""
        mod, base, tok = server
        for i in range(3):
            _req(base, "/snapshot", "PUT", f"v{i}".encode(), tok)
        import sqlite3
        with sqlite3.connect(tmp_path / "relay.db") as c:
            assert c.execute("SELECT COUNT(*) FROM snapshot").fetchone()[0] == 1

    def test_meta_reports_size_without_returning_the_bytes(self, server):
        _, base, tok = server
        _req(base, "/snapshot", "PUT", b"x" * 500, tok)
        code, body = _req(base, "/snapshot/meta", token=tok)
        meta = json.loads(body)
        assert code == 200 and meta["byte_len"] == 500 and meta["updated_at"] > 0

    def test_an_oversized_body_is_refused(self, server):
        _, base, tok = server
        mod = server[0]
        mod.MAX_BYTES = 100
        try:
            assert _req(base, "/snapshot", "PUT", b"x" * 500, tok)[0] == 413
        finally:
            mod.MAX_BYTES = 64 * 1024 * 1024


class TestOutbox:
    def test_an_item_can_be_added_listed_and_finished(self, server):
        import base64
        _, base, tok = server
        code, body = _req(base, "/outbox", "POST", b"sealed-1", tok,
                          {"X-Apex-Kind": "note"})
        assert code == 200
        item = json.loads(body)["id"]

        items = json.loads(_req(base, "/outbox", token=tok)[1])["items"]
        assert len(items) == 1
        assert items[0]["kind"] == "note"
        assert base64.b64decode(items[0]["ciphertext_b64"]) == b"sealed-1"

        assert json.loads(_req(base, f"/outbox/{item}/done", "POST", b"", tok)[1])["changed"] == 1
        assert json.loads(_req(base, "/outbox", token=tok)[1])["items"] == []

    def test_finishing_twice_reports_that_nothing_changed(self, server):
        """The laptop draining an item twice is a real bug, and a cheerful 200
        both times would hide it."""
        _, base, tok = server
        item = json.loads(_req(base, "/outbox", "POST", b"x", tok)[1])["id"]
        _req(base, f"/outbox/{item}/done", "POST", b"", tok)
        again = json.loads(_req(base, f"/outbox/{item}/done", "POST", b"", tok)[1])
        assert again["changed"] == 0

    def test_items_come_back_in_the_order_they_arrived(self, server):
        import base64
        _, base, tok = server
        for n in (b"first", b"second", b"third"):
            _req(base, "/outbox", "POST", n, tok)
        items = json.loads(_req(base, "/outbox", token=tok)[1])["items"]
        got = [base64.b64decode(i["ciphertext_b64"]) for i in items]
        assert got == [b"first", b"second", b"third"]

    def test_an_empty_item_is_refused(self, server):
        _, base, tok = server
        assert _req(base, "/outbox", "POST", b"", tok)[0] == 400


class TestItBindsLocalhostByDefault:
    def test_the_default_host_is_loopback(self, monkeypatch, tmp_path):
        """Run it on a VPS having forgotten to think about this, and it is
        reachable only from that box. Binding wider is one variable away and
        says so when it happens."""
        mod = _load()
        mod.TOKEN = "x"
        monkeypatch.delenv("RELAY_SERVER_HOST", raising=False)
        srv = mod.serve(port=_free_port(), db_path=str(tmp_path / "r.db"))
        try:
            assert srv.server_address[0] == "127.0.0.1"
        finally:
            srv.server_close()


class TestItCreatesItsOwnTables:
    """`tools/wiring_audit.orphan_init_db` deliberately skips `relay/`, because
    the relay is not part of Apex's process and Apex's boot must not initialise
    it. That exclusion removes a check, so this puts the check back where it
    belongs: not "does something call init_db" but "does a served request find
    the tables there".
    """

    def test_serving_creates_the_schema(self, tmp_path):
        import sqlite3
        mod = _load()
        mod.TOKEN = "x"
        db = tmp_path / "fresh.db"
        srv = mod.serve(host="127.0.0.1", port=_free_port(), db_path=str(db))
        try:
            with sqlite3.connect(db) as c:
                names = {r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            srv.server_close()
        assert {"snapshot", "outbox"} <= names

    def test_a_request_against_a_brand_new_database_works(self, tmp_path):
        """The end of the chain the audit would have guarded: a relay booted on
        an empty disk answers rather than 500ing on a missing table."""
        mod = _load()
        mod.TOKEN = "tok"
        port = _free_port()
        srv = mod.serve(host="127.0.0.1", port=port,
                        db_path=str(tmp_path / "brand-new.db"))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        try:
            assert _req(base, "/snapshot", "PUT", b"sealed", "tok")[0] == 200
            assert _req(base, "/snapshot", token="tok")[1] == b"sealed"
        finally:
            srv.shutdown()
            srv.server_close()
