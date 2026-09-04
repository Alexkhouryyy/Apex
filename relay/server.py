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

import hmac
import json
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
        c.commit()


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

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        # Deliberately before the auth gate and deliberately empty: a monitor
        # needs to know the process is alive without holding a credential, and
        # anything more here would be a fact about you served to strangers.
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if not self._gate():
            return
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
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO replies (created_at, question, answer, requests)"
                    " VALUES (?, ?, ?, ?)",
                    (time.time(), str(payload.get("question") or "")[:4000],
                     answer[:20000],
                     json.dumps(payload.get("requests") or [])[:8000]))
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
