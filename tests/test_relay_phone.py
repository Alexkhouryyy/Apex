"""Asking Apex from your phone while the laptop is shut.

The relay serves one page (/phone). A question waits in `questions` until the
optional answerer (relay/answer.py --watch) claims it, answers it from the
context the laptop pushed, and files the reply against it. Driven here against
a real relay process on a real port, with only the model call faked.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "phone-test-token"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def relay(tmp_path):
    srv_mod = _load("relay_server_phone", ROOT / "relay" / "server.py")
    srv_mod.TOKEN = TOKEN
    port = _free_port()
    srv = srv_mod.serve(host="127.0.0.1", port=port, db_path=str(tmp_path / "relay.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    ans = _load("relay_answer_phone", ROOT / "relay" / "answer.py")
    ans.SERVER, ans.TOKEN = base, TOKEN
    try:
        yield srv_mod, ans, base, str(tmp_path / "relay.db")
    finally:
        srv.shutdown()
        srv.server_close()


def req(base, path, method="GET", body=None, token=TOKEN):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    r = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def model_says(answer, requests=()):
    """A fake Anthropic call returning a JSON answer the way the model does."""
    def call(body):
        text = json.dumps({"answer": answer, "requests": list(requests)})
        return json.dumps({"content": [{"type": "text", "text": text}]}).encode()
    return call


def push_context(base, text="The user's dentist is on Tuesday at 3pm."):
    body = json.dumps({"context": {"text": text}, "tier": "cloud"}).encode()
    assert req(base, "/context", "PUT", body)[0] == 200


class TestThePage:

    def test_it_loads_without_a_token(self, relay):
        _m, _a, base, _db = relay
        status, body, headers = req(base, "/phone", token=None)
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"Apex, while you're away" in body
        assert req(base, "/", token=None)[0] == 200

    def test_it_carries_no_data(self, relay):
        """Served before the gate, so it must hold nothing about you."""
        _m, _a, base, _db = relay
        push_context(base, "SECRET-MARKER-42")
        _s, body, _h = req(base, "/phone", token=None)
        assert b"SECRET-MARKER-42" not in body and TOKEN.encode() not in body

    def test_nothing_loads_from_anywhere_else(self, relay):
        _m, _a, base, _db = relay
        _s, body, headers = req(base, "/phone", token=None)
        csp = headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp
        assert not re.search(rb"(src|href)=[\"']?https?:", body)

    def test_the_csp_hashes_match_what_is_served(self, relay):
        """Hash-pinned inline script and style. If the hash drifts from the
        page the browser refuses to run it — a dead page, silently."""
        _m, _a, base, _db = relay
        _s, body, headers = req(base, "/phone", token=None)
        csp = headers["Content-Security-Policy"]
        for tag in ("script", "style"):
            inner = re.search(rf"<{tag}>(.*?)</{tag}>".encode(), body, re.S).group(1)
            h = base64.b64encode(hashlib.sha256(inner).digest()).decode()
            assert f"'sha256-{h}'" in csp, f"{tag} hash not in CSP"


class TestQuestionsNeedTheToken:

    @pytest.mark.parametrize("path,method", [("/questions", "GET"), ("/questions", "POST"),
                                             ("/questions/1", "GET"), ("/status", "GET"),
                                             ("/questions/pending", "GET"),
                                             ("/questions/1/claim", "POST")])
    def test_refused_without_it(self, relay, path, method):
        _m, _a, base, _db = relay
        assert req(base, path, method, {"text": "hi"}, token=None)[0] == 401
        assert req(base, path, method, {"text": "hi"}, token="wrong")[0] == 401


class TestAskingAndAnswering:

    def test_a_question_is_answered_from_the_laptops_context(self, relay):
        _m, ans, base, _db = relay
        push_context(base)
        status, body, _h = req(base, "/questions", "POST", {"text": "When is my dentist?"})
        qid = json.loads(body)["id"]
        assert json.loads(req(base, f"/questions/{qid}")[1])["status"] == "queued"

        seen = {}

        def call(payload):
            seen["prompt"] = json.loads(payload)["messages"][0]["content"]
            return model_says("Tuesday at 3pm.")(payload)

        assert ans.watch_once(call=call) == 1
        assert "dentist is on Tuesday" in seen["prompt"], "the laptop's context was not used"
        q = json.loads(req(base, f"/questions/{qid}")[1])
        assert q["status"] == "answered" and q["answer"] == "Tuesday at 3pm."
        assert q["answered_at"] > 0

    def test_the_reply_still_reaches_the_laptop(self, relay):
        """The laptop files replies when it wakes; a phone question must not
        bypass that."""
        _m, ans, base, _db = relay
        push_context(base)
        qid = json.loads(req(base, "/questions", "POST", {"text": "hi"})[1])["id"]
        ans.watch_once(call=model_says("hello"))
        items = json.loads(req(base, "/replies")[1])["items"]
        assert [i["answer"] for i in items] == ["hello"]
        assert items[0]["question"] == "hi"

    def test_requested_actions_are_shown_as_queued_not_done(self, relay):
        _m, ans, base, _db = relay
        push_context(base)
        qid = json.loads(req(base, "/questions", "POST", {"text": "email Sam"})[1])["id"]
        ans.watch_once(call=model_says("I'll leave that for your laptop.",
                                       [{"tool": "send_email", "why": "you asked"}]))
        q = json.loads(req(base, f"/questions/{qid}")[1])
        assert q["requests"] == [{"tool": "send_email", "why": "you asked"}]

    def test_a_failed_answer_says_why_instead_of_spinning(self, relay):
        _m, ans, base, _db = relay
        push_context(base)
        qid = json.loads(req(base, "/questions", "POST", {"text": "hi"})[1])["id"]

        def boom(_payload):
            raise RuntimeError("model quota exhausted")
        assert ans.watch_once(call=boom, log=lambda *a: None) == 0
        q = json.loads(req(base, f"/questions/{qid}")[1])
        assert q["status"] == "failed" and "model quota exhausted" in q["error"]

    def test_the_status_says_whether_anyone_is_answering(self, relay):
        _m, ans, base, _db = relay
        s = json.loads(req(base, "/status")[1])
        assert s["answerer_seen_at"] == 0, "nobody has polled yet"
        ans.watch_once(call=model_says("x"))
        s = json.loads(req(base, "/status")[1])
        assert s["answerer_seen_at"] > 0 and s["now"] - s["answerer_seen_at"] < 5

    def test_the_list_shows_recent_questions_newest_first(self, relay):
        _m, _a, base, _db = relay
        for t in ("one", "two", "three"):
            req(base, "/questions", "POST", {"text": t})
        items = json.loads(req(base, "/questions")[1])["items"]
        assert [i["text"] for i in items] == ["three", "two", "one"]


class TestClaiming:

    def test_two_answerers_cannot_take_one_question(self, relay):
        _m, _a, base, _db = relay
        qid = json.loads(req(base, "/questions", "POST", {"text": "hi"})[1])["id"]
        first = json.loads(req(base, f"/questions/{qid}/claim", "POST", {})[1])
        second = json.loads(req(base, f"/questions/{qid}/claim", "POST", {})[1])
        assert (first["changed"], second["changed"]) == (1, 0)

    def test_a_claim_abandoned_mid_answer_is_offered_again(self, relay):
        srv_mod, _a, base, _db = relay
        qid = json.loads(req(base, "/questions", "POST", {"text": "hi"})[1])["id"]
        req(base, f"/questions/{qid}/claim", "POST", {})
        assert json.loads(req(base, "/questions/pending")[1])["items"] == []
        srv_mod.CLAIM_SECONDS = -1                 # the answerer died long ago
        assert [i["id"] for i in json.loads(req(base, "/questions/pending")[1])["items"]] == [qid]

    def test_an_answered_question_cannot_be_failed_afterwards(self, relay):
        _m, ans, base, _db = relay
        push_context(base)
        qid = json.loads(req(base, "/questions", "POST", {"text": "hi"})[1])["id"]
        ans.watch_once(call=model_says("done"))
        r = json.loads(req(base, f"/questions/{qid}/error", "POST", {"error": "late"})[1])
        assert r["changed"] == 0
        assert json.loads(req(base, f"/questions/{qid}")[1])["status"] == "answered"


class TestLimits:

    @pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"nope": 1}, b"{broken"])
    def test_non_questions_are_refused(self, relay, body):
        _m, _a, base, _db = relay
        assert req(base, "/questions", "POST", body)[0] == 400

    def test_an_essay_is_refused(self, relay):
        _m, _a, base, _db = relay
        assert req(base, "/questions", "POST", {"text": "x" * 2001})[0] == 400

    def test_a_pile_of_unanswered_questions_is_capped(self, relay):
        srv_mod, _a, base, _db = relay
        srv_mod.MAX_PENDING_QUESTIONS = 3
        for i in range(3):
            assert req(base, "/questions", "POST", {"text": f"q{i}"})[0] == 200
        status, body, _h = req(base, "/questions", "POST", {"text": "one more"})
        assert status == 429 and b"answerer" in body


def test_a_deployed_relay_gains_the_new_column(tmp_path):
    """A relay already running has a `replies` table without question_id;
    starting the new server.py must add it, not crash on the first reply."""
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE replies (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                  " created_at REAL NOT NULL, question TEXT NOT NULL DEFAULT '',"
                  " answer TEXT NOT NULL DEFAULT '', requests TEXT NOT NULL DEFAULT '[]',"
                  " done_at REAL NOT NULL DEFAULT 0)")
    srv_mod = _load("relay_server_migrate", ROOT / "relay" / "server.py")
    srv_mod.init_db(str(db))
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(replies)")}
    assert "question_id" in cols


def test_the_watch_log_is_flushed_for_systemd():
    """Under systemd stdout is a pipe; an unflushed line never shows in
    `systemctl status`, and docs/RELAY_DEPLOY.md tells you to look there."""
    import subprocess
    import sys
    code = ("import importlib.util as u; s=u.spec_from_file_location('a', 'relay/answer.py');"
            " m=u.module_from_spec(s); s.loader.exec_module(m); m._log('hello from the answerer');"
            " import time; time.sleep(30)")
    import os
    # As systemd runs it: no PYTHONUNBUFFERED. Left set (as some shells and CI
    # do), it hides exactly the buffering this test is about.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    p = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT),
                         stdout=subprocess.PIPE, text=True, env=env)
    got = []
    reader = threading.Thread(target=lambda: got.append(p.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout=5)                  # unflushed, it only arrives at exit
    p.kill()
    assert got and got[0].strip() == "hello from the answerer", \
        "the log line did not arrive while the process was running"
