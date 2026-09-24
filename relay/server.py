"""The always-on relay — a mailbox for Apex, not a second Apex.

Step 2 of `docs/PHASE_6_7_PLAN.md`. This is the only part of Apex that runs on a
machine you may not own, so it is built to the opposite of the usual brief:
the goal is for it to be able to do as little as possible.

## What it does, in full

Stores two things and hands them back:

  * `snapshot` — one row, the sealed copy of Apex's memory. The phone reads it
    when the laptop is unreachable.
  * `outbox` — work that arrived while the laptop was off, drained in order
    when it comes back.

And serves one page, `/phone`, so you can ask Apex something from your phone
while the laptop is shut. The page holds no data; everything it shows comes
through the same token-gated routes as the rest. A question waits in
`questions` until the optional answerer (relay/answer.py --watch) picks it up.

It does not reason. It holds no model key. It cannot open anything it stores.

## Three deliberate constraints

**It imports nothing from Apex.** Not `config`, not `agent`, not a shared
helper. One file, standard library only, deployable by copying it to a box with
Python on it and nothing else. That is not tidiness: every dependency on the
relay is a supply chain attached to the machine holding your memory, and every
import from `agent/` is a way for a change over here to quietly widen what runs
over there. `tests/test_relay_server.py` asserts both.

**It binds 127.0.0.1 unless told otherwise.** Run it on a VPS with a default
config and it is reachable only from that box; put Caddy, nginx or
`tailscale serve` in front to terminate TLS. Binding every interface is one
environment variable away and says what it is doing when it happens — but it is
not what you get by forgetting to think about it.

**No token configured means nothing is served.** Not "no token means no check".
That inversion is this project's most-repeated bug: Apex's own dashboard
middleware waved every request through when `DASHBOARD_TOKEN` was empty, which
is how a real authorisation bug on `/board` stayed invisible for weeks. Here the
same mistake would publish a stranger's entire memory to the internet, so an
unconfigured relay refuses everything and says why.

## Configuration, all of it

    RELAY_SERVER_TOKEN     required; the shared secret the laptop presents
    RELAY_SERVER_DB        default ./relay.db
    RELAY_SERVER_HOST      default 127.0.0.1
    RELAY_SERVER_PORT      default 8799
    RELAY_SERVER_MAX_BYTES default 67108864 (64 MiB)

Note what is absent: there is no key here, and there is nowhere to put one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.getenv("RELAY_SERVER_DB", "relay.db")
TOKEN = os.getenv("RELAY_SERVER_TOKEN", "")
MAX_BYTES = int(os.getenv("RELAY_SERVER_MAX_BYTES", str(64 * 1024 * 1024)))


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: str | None = None) -> None:
    with connect(path) as c:
        # Sealed bytes only. There is no column here for anything readable, and
        # that is the schema making the promise rather than a comment doing it.
        c.execute("""
            CREATE TABLE IF NOT EXISTS snapshot (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at REAL NOT NULL,
                byte_len   INTEGER NOT NULL,
                ciphertext BLOB NOT NULL
            )
        """)
        # The one table here that holds READABLE text. It exists because a box
        # that cannot read cannot answer; everything else is ciphertext. Kept
        # separate from `snapshot` so the distinction is in the schema rather
        # than in a comment somebody has to find.
        c.execute("""
            CREATE TABLE IF NOT EXISTS context (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at REAL NOT NULL,
                body       TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                kind       TEXT NOT NULL DEFAULT '',
                ciphertext BLOB NOT NULL,
                done_at    REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_outbox_pending "
                  "ON outbox(done_at, id)")
        # Replies written by the optional answerer (relay/answer.py). Plaintext,
        # and separate from `outbox` on purpose: outbox items come from YOUR
        # devices and are sealed, replies are written on this box by something
        # that had to read the context to produce them, so sealing them would be
        # theatre. Two origins, two trust levels, two tables.
        c.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                question   TEXT NOT NULL DEFAULT '',
                answer     TEXT NOT NULL DEFAULT '',
                requests   TEXT NOT NULL DEFAULT '[]',
                done_at    REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_replies_pending "
                  "ON replies(done_at, id)")
        # Which question a reply answers, for the phone page. Added to a table
        # that may already exist on a deployed box, so it is a migration, not
        # a column in the CREATE above.
        cols = {r[1] for r in c.execute("PRAGMA table_info(replies)")}
        if "question_id" not in cols:
            c.execute("ALTER TABLE replies ADD COLUMN question_id INTEGER")
        # Questions asked from the phone page. Plaintext, like `replies` and
        # for the same reason: the answerer has to read them to answer them.
        # They come from your phone over your tailnet with your token — the
        # same trust as the context the laptop pushes.
        c.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  REAL NOT NULL,
                text        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'answering', 'answered', 'failed')),
                claimed_at  REAL NOT NULL DEFAULT 0,
                answered_at REAL NOT NULL DEFAULT 0,
                reply_id    INTEGER,
                error       TEXT NOT NULL DEFAULT ''
            )
        """)
        # When the answerer last asked for work — so the page can say "nobody
        # is answering" instead of spinning forever.
        c.execute("""
            CREATE TABLE IF NOT EXISTS heartbeat (
                id      INTEGER PRIMARY KEY CHECK (id = 1),
                seen_at REAL NOT NULL
            )
        """)
        c.commit()


# A question claimed but not answered within this long is offered again: the
# answerer died mid-question, and the phone should not wait forever.
CLAIM_SECONDS = 180
MAX_PENDING_QUESTIONS = 50
MAX_QUESTION_CHARS = 2000


def _question_row(r) -> dict:
    keys = ("id", "created_at", "text", "status", "claimed_at", "answered_at",
            "error", "answer", "requests")
    out = dict(zip(keys, r))
    try:
        out["requests"] = json.loads(out["requests"] or "[]")
    except Exception:
        out["requests"] = []
    return out


_QUESTION_SELECT = ("SELECT q.id, q.created_at, q.text, q.status, q.claimed_at,"
                    " q.answered_at, q.error, r.answer, r.requests"
                    " FROM questions q LEFT JOIN replies r ON r.id = q.reply_id")


def authorised(header: str | None) -> tuple[bool, str]:
    """(ok, why-not). Constant-time, and closed when unconfigured.

    `hmac.compare_digest` rather than `==` because a plain comparison returns as
    soon as two bytes differ, which leaks the token one character at a time to
    anyone patient enough to measure.
    """
    if not TOKEN:
        return False, ("this relay has no RELAY_SERVER_TOKEN set, so it serves "
                       "nothing. Set one and restart it.")
    if not header or not header.startswith("Bearer "):
        return False, "missing bearer token"
    if not hmac.compare_digest(header[7:], TOKEN):
        return False, "bad token"
    return True, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "ApexRelay/1"
    db_path: str | None = None          # overridden per-server in tests

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        sys.stderr.write("[relay] %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes = b"",
              ctype: str = "application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _gate(self) -> bool:
        ok, why = authorised(self.headers.get("Authorization"))
        if not ok:
            self._json(401, {"error": why})
        return ok

    def _body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BYTES:
            self._json(413, {"error": f"body exceeds {MAX_BYTES} bytes"})
            return None
        return self.rfile.read(n) if n else b""

    def _conn(self):
        return connect(self.db_path)

    def _page(self):
        body = PHONE_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Nothing may load from anywhere else, and the one inline script and
        # style are pinned by hash — an injected <script> would not run.
        self.send_header("Content-Security-Policy", PHONE_CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        # Deliberately before the auth gate and deliberately empty: a monitor
        # needs to know the process is alive without holding a credential, and
        # anything more here would be a fact about you served to strangers.
        if self.path == "/health":
            return self._json(200, {"ok": True})
        # The phone page, before the gate: it is a static page with no data in
        # it, and it has to load before you can type the token into it.
        if self.path in ("/phone", "/"):
            return self._page()
        if not self._gate():
            return
        if self.path == "/status":
            with self._conn() as c:
                ctx = c.execute("SELECT updated_at FROM context WHERE id = 1").fetchone()
                snap = c.execute("SELECT updated_at FROM snapshot WHERE id = 1").fetchone()
                beat = c.execute("SELECT seen_at FROM heartbeat WHERE id = 1").fetchone()
            return self._json(200, {"now": time.time(),
                                    "context_at": ctx[0] if ctx else 0,
                                    "snapshot_at": snap[0] if snap else 0,
                                    "answerer_seen_at": beat[0] if beat else 0})
        if self.path == "/questions/pending":
            now = time.time()
            with self._conn() as c:
                c.execute("INSERT INTO heartbeat (id, seen_at) VALUES (1, ?)"
                          " ON CONFLICT(id) DO UPDATE SET seen_at=excluded.seen_at", (now,))
                rows = c.execute(
                    "SELECT id, text FROM questions WHERE status = 'queued'"
                    " OR (status = 'answering' AND claimed_at < ?) ORDER BY id",
                    (now - CLAIM_SECONDS,)).fetchall()
                c.commit()
            return self._json(200, {"items": [{"id": r[0], "text": r[1]} for r in rows]})
        if self.path == "/questions":
            with self._conn() as c:
                rows = c.execute(_QUESTION_SELECT + " ORDER BY q.id DESC LIMIT 20").fetchall()
            return self._json(200, {"items": [_question_row(r) for r in rows]})
        m = re.fullmatch(r"/questions/(\d+)", self.path)
        if m:
            with self._conn() as c:
                row = c.execute(_QUESTION_SELECT + " WHERE q.id = ?",
                                (int(m.group(1)),)).fetchone()
            if not row:
                return self._json(404, {"error": "no such question"})
            return self._json(200, _question_row(row))
        if self.path == "/snapshot":
            with self._conn() as c:
                row = c.execute("SELECT ciphertext, updated_at FROM snapshot "
                                "WHERE id = 1").fetchone()
            if not row:
                return self._json(404, {"error": "no snapshot stored yet"})
            return self._send(200, row[0])
        if self.path == "/snapshot/meta":
            with self._conn() as c:
                row = c.execute("SELECT updated_at, byte_len FROM snapshot "
                                "WHERE id = 1").fetchone()
            return self._json(200, {"updated_at": row[0], "byte_len": row[1]}
                              if row else {"updated_at": 0, "byte_len": 0})
        if self.path == "/context":
            with self._conn() as c:
                row = c.execute("SELECT body, updated_at FROM context "
                                "WHERE id = 1").fetchone()
            if not row:
                return self._json(404, {"error": "no context stored yet"})
            return self._send(200, row[0].encode(), "application/json")
        if self.path == "/replies":
            with self._conn() as c:
                rows = c.execute(
                    "SELECT id, created_at, question, answer, requests"
                    " FROM replies WHERE done_at = 0 ORDER BY id").fetchall()
            return self._json(200, {"items": [
                {"id": r[0], "created_at": r[1], "question": r[2],
                 "answer": r[3], "requests": r[4]} for r in rows]})
        if self.path == "/outbox":
            with self._conn() as c:
                rows = c.execute(
                    "SELECT id, created_at, kind, ciphertext FROM outbox "
                    "WHERE done_at = 0 ORDER BY id").fetchall()
            import base64
            return self._json(200, {"items": [
                {"id": r[0], "created_at": r[1], "kind": r[2],
                 "ciphertext_b64": base64.b64encode(r[3]).decode()}
                for r in rows]})
        return self._json(404, {"error": "no such path"})

    def do_PUT(self):
        if not self._gate():
            return
        if self.path == "/context":
            body = self._body()
            if body is None:
                return
            if not body:
                return self._json(400, {"error": "empty context refused"})
            with self._conn() as c:
                c.execute(
                    "INSERT INTO context (id, updated_at, body) VALUES (1, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,"
                    " body=excluded.body", (time.time(), body.decode("utf-8", "replace")))
                c.commit()
            return self._json(200, {"ok": True, "chars": len(body)})
        if self.path != "/snapshot":
            return self._json(404, {"error": "no such path"})
        body = self._body()
        if body is None:
            return
        if not body:
            return self._json(400, {"error": "empty snapshot refused"})
        with self._conn() as c:
            c.execute(
                "INSERT INTO snapshot (id, updated_at, byte_len, ciphertext) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "updated_at=excluded.updated_at, byte_len=excluded.byte_len, "
                "ciphertext=excluded.ciphertext",
                (time.time(), len(body), body))
            c.commit()
        return self._json(200, {"ok": True, "byte_len": len(body)})

    def do_POST(self):
        if not self._gate():
            return
        if self.path == "/questions":
            body = self._body()
            if body is None:
                return
            try:
                text = str(json.loads(body or b"{}").get("text") or "").strip()
            except Exception:
                return self._json(400, {"error": "a question is JSON: {\"text\": \"...\"}"})
            if not text:
                return self._json(400, {"error": "empty question refused"})
            if len(text) > MAX_QUESTION_CHARS:
                return self._json(400, {"error": f"keep it under {MAX_QUESTION_CHARS} characters"})
            with self._conn() as c:
                pending = c.execute("SELECT COUNT(*) FROM questions WHERE status IN"
                                    " ('queued', 'answering')").fetchone()[0]
                if pending >= MAX_PENDING_QUESTIONS:
                    return self._json(429, {"error": "too many unanswered questions — is "
                                                     "the answerer running?"})
                cur = c.execute("INSERT INTO questions (created_at, text) VALUES (?, ?)",
                                (time.time(), text))
                c.commit()
            return self._json(200, {"ok": True, "id": cur.lastrowid})
        m = re.fullmatch(r"/questions/(\d+)/(claim|error)", self.path)
        if m:
            qid, action = int(m.group(1)), m.group(2)
            now = time.time()
            with self._conn() as c:
                if action == "claim":
                    # Atomic: two answerers cannot both take one question.
                    cur = c.execute(
                        "UPDATE questions SET status = 'answering', claimed_at = ?"
                        " WHERE id = ? AND (status = 'queued' OR"
                        " (status = 'answering' AND claimed_at < ?))",
                        (now, qid, now - CLAIM_SECONDS))
                else:
                    body = self._body()
                    if body is None:
                        return
                    try:
                        err = str(json.loads(body or b"{}").get("error") or "")[:500]
                    except Exception:
                        err = ""
                    cur = c.execute(
                        "UPDATE questions SET status = 'failed', error = ?"
                        " WHERE id = ? AND status IN ('queued', 'answering')",
                        (err or "the answerer failed without saying why", qid))
                c.commit()
            return self._json(200, {"ok": True, "changed": cur.rowcount})
        if self.path == "/outbox":
            body = self._body()
            if body is None:
                return
            if not body:
                return self._json(400, {"error": "empty item refused"})
            kind = (self.headers.get("X-Apex-Kind") or "")[:64]
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO outbox (created_at, kind, ciphertext) "
                    "VALUES (?, ?, ?)", (time.time(), kind, body))
                c.commit()
            return self._json(200, {"ok": True, "id": cur.lastrowid})
        if self.path == "/reply":
            body = self._body()
            if body is None:
                return
            try:
                payload = json.loads(body or b"{}")
            except Exception as e:
                return self._json(400, {"error": f"reply is not JSON: {e}"})
            answer = str(payload.get("answer") or "").strip()
            if not answer:
                return self._json(400, {"error": "empty answer refused"})
            qid = payload.get("question_id")
            qid = qid if isinstance(qid, int) and not isinstance(qid, bool) else None
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO replies (created_at, question, answer, requests,"
                    " question_id) VALUES (?, ?, ?, ?, ?)",
                    (time.time(), str(payload.get("question") or "")[:4000],
                     answer[:20000],
                     json.dumps(payload.get("requests") or [])[:8000], qid))
                if qid is not None:
                    c.execute("UPDATE questions SET status = 'answered', reply_id = ?,"
                              " answered_at = ? WHERE id = ?",
                              (cur.lastrowid, time.time(), qid))
                c.commit()
            return self._json(200, {"ok": True, "id": cur.lastrowid})
        if self.path.startswith("/replies/") and self.path.endswith("/done"):
            try:
                item = int(self.path.split("/")[2])
            except (IndexError, ValueError):
                return self._json(400, {"error": "bad reply id"})
            with self._conn() as c:
                cur = c.execute(
                    "UPDATE replies SET done_at = ? WHERE id = ? AND done_at = 0",
                    (time.time(), item))
                c.commit()
            return self._json(200, {"ok": True, "changed": cur.rowcount})
        if self.path.startswith("/outbox/") and self.path.endswith("/done"):
            try:
                item = int(self.path.split("/")[2])
            except (IndexError, ValueError):
                return self._json(400, {"error": "bad item id"})
            with self._conn() as c:
                cur = c.execute(
                    "UPDATE outbox SET done_at = ? WHERE id = ? AND done_at = 0",
                    (time.time(), item))
                c.commit()
            # 0 rows means already done or never existed. Reported rather than
            # smoothed over: the laptop draining an item twice is a real bug and
            # a cheerful 200 would hide it.
            return self._json(200, {"ok": True, "changed": cur.rowcount})
        return self._json(404, {"error": "no such path"})


# ── the phone page ───────────────────────────────────────────────────────────
#
# One page, no data in it, nothing loaded from anywhere else. Everything it
# shows comes from the token-gated routes above, and every answer goes in via
# textContent — an answer is model output, and model output is not markup.
PHONE_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1116">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Apex · away</title>
<style>
:root{color-scheme:dark;--bg:#0b1116;--panel:#121b22;--line:#243240;--ink:#e6eef3;--dim:#8ba0ab;--accent:#7aebc7;--warn:#ffcf8a;--bad:#ff9b8a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:max(16px,env(safe-area-inset-top)) 16px max(16px,env(safe-area-inset-bottom))}
main{max-width:640px;margin:0 auto;display:flex;flex-direction:column;gap:14px}
h1{font-size:20px;margin:4px 0 0;letter-spacing:-.02em}
.sub{color:var(--dim);font-size:13px;margin:0}
.status{display:flex;flex-wrap:wrap;gap:6px}
.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:3px 10px;color:var(--dim)}
.pill.ok{color:var(--accent);border-color:#2d5b50}.pill.warn{color:var(--warn);border-color:#5b4a2d}.pill.bad{color:var(--bad);border-color:#5b302d}
form{display:flex;flex-direction:column;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px}
textarea,input{width:100%;background:transparent;border:0;color:var(--ink);font:inherit;outline:none;resize:vertical}
textarea{min-height:64px}
input{border:1px solid var(--line);border-radius:10px;padding:10px}
button{align-self:flex-end;background:var(--accent);color:#0b2a22;border:0;border-radius:10px;padding:10px 16px;font:inherit;font-weight:600}
button:disabled{opacity:.45}
button.link{background:none;color:var(--dim);padding:4px 0;font-weight:400;font-size:12px;align-self:auto}
.qa{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px;display:flex;flex-direction:column;gap:6px}
.q{font-weight:600}.a{white-space:pre-wrap;overflow-wrap:anywhere}
.meta{font-size:12px;color:var(--dim)}.meta.warn{color:var(--warn)}.meta.bad{color:var(--bad)}
ul.req{margin:0;padding-left:18px;font-size:13px;color:var(--warn)}
[hidden]{display:none!important}
</style></head>
<body><main>
<div><h1>Apex, while you're away</h1>
<p class="sub">Answers come from the summary your laptop last sent. Nothing here can act — anything that needs your computer is queued for when it wakes.</p></div>
<div class="status" id="status"></div>
<form id="login" hidden>
<label class="sub" for="token">Relay token (the RELAY_TOKEN from your laptop's .env)</label>
<input id="token" type="password" autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">Save</button>
</form>
<form id="ask" hidden>
<textarea id="text" maxlength="2000" placeholder="Ask Apex something…"></textarea>
<button type="submit" id="send">Ask</button>
</form>
<div id="error" class="meta bad" hidden></div>
<section id="list"></section>
<button class="link" id="forget" hidden>Forget the token on this phone</button>
</main>
<script>
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KEY = 'apex_relay_token';
  const store = {
    get() { try { return localStorage.getItem(KEY) || ''; } catch (_) { return ''; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (_) {} },
    clear() { try { localStorage.removeItem(KEY); } catch (_) {} },
  };
  const ago = s => {
    if (!s) return 'never';
    const d = Math.max(0, Date.now() / 1000 - s);
    if (d < 90) return Math.round(d) + 's ago';
    if (d < 5400) return Math.round(d / 60) + ' min ago';
    if (d < 172800) return Math.round(d / 3600) + ' h ago';
    return Math.round(d / 86400) + ' days ago';
  };
  function error(text) { $('error').textContent = text || ''; $('error').hidden = !text; }
  async function api(path, opts = {}) {
    const r = await fetch(path, {...opts, headers: {'Authorization': 'Bearer ' + store.get(),
      'Content-Type': 'application/json', ...(opts.headers || {})}});
    if (r.status === 401) { showLogin('That token was refused. Check RELAY_TOKEN in your laptop’s .env.'); throw new Error('unauthorised'); }
    let data = {};
    try { data = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(data.error || ('Request failed (' + r.status + ')'));
    return data;
  }
  function pill(text, cls) { const s = document.createElement('span'); s.className = 'pill ' + cls; s.textContent = text; return s; }
  let answererSeen = 0;
  async function status() {
    const s = await api('/status');
    answererSeen = s.answerer_seen_at;
    const live = s.answerer_seen_at && s.now - s.answerer_seen_at < 30;
    const fresh = s.context_at && s.now - s.context_at < 7200;
    $('status').replaceChildren(
      pill(live ? 'Answerer online' : 'Answerer offline — start answer.py --watch', live ? 'ok' : 'bad'),
      pill('Laptop last sent ' + ago(s.context_at), s.context_at ? (fresh ? 'ok' : 'warn') : 'bad'));
  }
  const LABEL = {queued: 'Waiting for the answerer…', answering: 'Answering…', failed: 'Failed', answered: ''};
  function render(q) {
    let el = document.getElementById('q' + q.id);
    if (!el) {
      el = document.createElement('article'); el.className = 'qa'; el.id = 'q' + q.id;
      const qt = document.createElement('div'); qt.className = 'q';
      const a = document.createElement('div'); a.className = 'a';
      const m = document.createElement('div'); m.className = 'meta';
      const r = document.createElement('ul'); r.className = 'req'; r.hidden = true;
      el.append(qt, a, r, m);
      $('list').prepend(el);
    }
    const [qt, a, r, m] = el.children;
    qt.textContent = q.text;
    a.textContent = q.status === 'answered' ? (q.answer || '') : '';
    const reqs = Array.isArray(q.requests) ? q.requests : [];
    r.replaceChildren(...reqs.map(x => { const li = document.createElement('li');
      li.textContent = 'Queued for your laptop: ' + (x.tool || 'an action') + (x.why ? ' — ' + x.why : ''); return li; }));
    r.hidden = !reqs.length;
    m.className = 'meta';
    if (q.status === 'answered') m.textContent = 'Answered ' + ago(q.answered_at);
    else if (q.status === 'failed') { m.className = 'meta bad'; m.textContent = 'Failed: ' + (q.error || 'no reason given'); }
    else {
      const waited = Date.now() / 1000 - q.created_at;
      const stuck = q.status === 'queued' && waited > 20 && !(answererSeen && Date.now() / 1000 - answererSeen < 30);
      m.className = stuck ? 'meta warn' : 'meta';
      m.textContent = stuck ? 'Nobody is picking this up. Is answer.py --watch running on the relay?' : LABEL[q.status];
    }
  }
  const waiting = new Set();
  async function poll() {
    for (const id of [...waiting]) {
      try {
        const q = await api('/questions/' + id); render(q);
        if (q.status === 'answered' || q.status === 'failed') waiting.delete(id);
      } catch (e) { if (e.message !== 'unauthorised') error(e.message); }
    }
  }
  async function load() {
    const data = await api('/questions');
    $('list').replaceChildren();
    for (const q of data.items.slice().reverse()) {
      render(q);
      if (q.status === 'queued' || q.status === 'answering') waiting.add(q.id);
    }
  }
  function showLogin(msg) {
    $('login').hidden = false; $('ask').hidden = true; $('forget').hidden = true;
    error(msg || '');
  }
  async function start() {
    if (!store.get()) { showLogin(); return; }
    $('login').hidden = true; $('ask').hidden = false; $('forget').hidden = false; error('');
    try { await status(); await load(); } catch (e) { if (e.message !== 'unauthorised') error(e.message); }
  }
  $('login').onsubmit = ev => { ev.preventDefault(); const t = $('token').value.trim(); if (!t) return;
    store.set(t); $('token').value = ''; start(); };
  $('forget').onclick = () => { store.clear(); $('list').replaceChildren(); $('status').replaceChildren(); showLogin(); };
  $('ask').onsubmit = async ev => {
    ev.preventDefault();
    const text = $('text').value.trim(); if (!text) return;
    $('send').disabled = true; error('');
    try {
      const {id} = await api('/questions', {method: 'POST', body: JSON.stringify({text})});
      $('text').value = '';
      render({id, text, status: 'queued', created_at: Date.now() / 1000});
      waiting.add(id);
    } catch (e) { if (e.message !== 'unauthorised') error(e.message); }
    finally { $('send').disabled = false; }
  };
  setInterval(() => { if (store.get() && !$('ask').hidden) poll(); }, 1500);
  setInterval(() => { if (store.get() && !$('ask').hidden) status().catch(() => {}); }, 15000);
  start();
})();
</script>
</body></html>
"""


def _csp_hash(tag: str) -> str:
    """sha256 of the one inline <tag> block, for the Content-Security-Policy."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", PHONE_PAGE, re.S)
    digest = hashlib.sha256(m.group(1).encode()).digest()
    return "'sha256-" + base64.b64encode(digest).decode() + "'"


PHONE_CSP = ("default-src 'none'; connect-src 'self'; "
             f"script-src {_csp_hash('script')}; style-src {_csp_hash('style')}; "
             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def serve(host: str | None = None, port: int | None = None,
          db_path: str | None = None) -> ThreadingHTTPServer:
    host = host or os.getenv("RELAY_SERVER_HOST", "127.0.0.1")
    port = int(port if port is not None else os.getenv("RELAY_SERVER_PORT", "8799"))
    init_db(db_path)
    handler = type("BoundHandler", (Handler,), {"db_path": db_path or DB_PATH})
    return ThreadingHTTPServer((host, port), handler)


def main() -> int:
    if not TOKEN:
        print("Refusing to start: RELAY_SERVER_TOKEN is not set.\n"
              "An unauthenticated relay would hand your sealed memory to "
              "anyone who found the URL. Generate a long random string, put it "
              "in the environment here AND in the laptop's RELAY_TOKEN, and "
              "start again.", file=sys.stderr)
        return 2
    srv = serve()
    host, port = srv.server_address[:2]
    print(f"[relay] listening on {host}:{port}, db={DB_PATH}")
    if host not in ("127.0.0.1", "::1", "localhost"):
        print("[relay] NOTE: bound beyond localhost. Terminate TLS in front of "
              "this (Caddy, nginx, or `tailscale serve`) — it speaks plain "
              "HTTP.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
