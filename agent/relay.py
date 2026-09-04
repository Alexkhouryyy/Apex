"""Sealing, snapshotting and moving Apex's memory to an always-on relay.

Step 1 of `docs/PHASE_6_7_PLAN.md`. This half has no server yet on purpose: the
parts that decide anything — what gets sealed, what refuses, what a failure
looks like — are here and testable, and the transport is one injectable
function so a fake one is a real test rather than a mock of the interesting
logic.

## What this is for

The laptop is Apex and remains the sole author of memory. The relay is an
always-on box that holds two things while the laptop sleeps: an outbox of work
that arrived, and the last snapshot of memory so the phone still has something
to read. It never reasons and it cannot read the snapshot.

## The one rule that matters here

**Nothing leaves without a key.** `seal()` raises when `RELAY_KEY` is absent,
short, or not a real Fernet key — it never falls back to sending plaintext.

That is not defensive habit, it is the whole feature. "Encryption is on unless
it isn't" is the exact fail-open shape this codebase keeps finding: a
misconfigured `.env` would push a readable copy of every memory to a rented
machine, and everything downstream — the dashboard, the logs, the phone —
would look identical either way. There is no observation from outside that
distinguishes a sealed push from an unsealed one, so the refusal has to happen
here, at the only place that can tell.

For the same reason a passphrase is refused rather than stretched into a key.
Deriving one from `hunter2` is defensible if it is done with a salt and a
proper KDF, and indefensible done casually — and the casual version looks
exactly like the careful one from the outside. `new_key()` prints a real one.

## Why Fernet

`cryptography` is already a dependency (Ed25519 for inbound Discord webhooks),
so this adds nothing to install. Fernet is AES-128-CBC with an HMAC-SHA256 tag,
which matters for a reason beyond confidentiality: the relay is a machine we do
not trust to read the snapshot, so it is also a machine we should not trust to
return the snapshot unmodified. Authenticated encryption makes a tampered
snapshot fail to open instead of opening as something else.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import config
from agent import longterm


class RelayError(RuntimeError):
    """Raised for anything that must not be allowed to degrade quietly."""


# ── keys and sealing ─────────────────────────────────────────────────────────

def new_key() -> str:
    """Generate a key. Printed by `python -m agent.relay --new-key`."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _fernet(key: Optional[str] = None):
    from cryptography.fernet import Fernet
    raw = (key if key is not None else getattr(config, "RELAY_KEY", "")) or ""
    raw = raw.strip()
    if not raw:
        raise RelayError(
            "RELAY_KEY is not set, so nothing can be sealed and nothing will be "
            "sent. Generate one with `python -m agent.relay --new-key` and put "
            "it in .env. It must never be given to the relay.")
    try:
        return Fernet(raw.encode())
    except Exception:
        raise RelayError(
            "RELAY_KEY is not a valid key. It is not a password — it is 32 "
            "random bytes in url-safe base64. Generate one with "
            "`python -m agent.relay --new-key`. Apex will not stretch a "
            "passphrase into a key for you, because a weak key and a strong "
            "one look identical from everywhere except here.")


def seal(plaintext: bytes, key: Optional[str] = None) -> bytes:
    """Encrypt. Raises rather than returning plaintext — see the module docstring."""
    if not isinstance(plaintext, (bytes, bytearray)):
        raise RelayError(f"seal() takes bytes, got {type(plaintext).__name__}")
    return _fernet(key).encrypt(bytes(plaintext))


def unseal(ciphertext: bytes, key: Optional[str] = None) -> bytes:
    """Decrypt, verifying the tag. A tampered or truncated payload raises."""
    from cryptography.fernet import InvalidToken
    try:
        return _fernet(key).decrypt(bytes(ciphertext))
    except InvalidToken:
        raise RelayError(
            "The payload did not decrypt. Either RELAY_KEY has changed since it "
            "was sealed, or the relay returned something other than what was "
            "stored. Both are worth knowing about; neither is recoverable by "
            "retrying.")


# ── snapshots ────────────────────────────────────────────────────────────────

def snapshot_bytes(db_path: Optional[str] = None) -> bytes:
    """A consistent copy of the whole brain, as bytes.

    Uses SQLite's online backup API rather than reading the file, for the reason
    `scripts/backup_brain.py` already documents: Apex runs in WAL mode, so the
    newest writes live in a `-wal` sidecar and a plain file read silently drops
    them. It also works while Apex is running, which a snapshot on a timer has
    to.

    Integrity is checked before the bytes are returned. A snapshot nobody opened
    is a promise, not a copy — and this one is going to a machine that cannot
    tell us it is broken.
    """
    src = Path(db_path or longterm.DB_PATH)
    if not src.exists():
        raise RelayError(f"no database at {src}")

    with tempfile.TemporaryDirectory(prefix="apex_snap_") as tmp:
        dest = Path(tmp) / "snapshot.db"
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with sqlite3.connect(f"file:{dest}?mode=ro", uri=True) as c:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RelayError(f"snapshot failed its integrity check: {ok}")
        return dest.read_bytes()


# ── transport ────────────────────────────────────────────────────────────────

# Replaced wholesale in tests. One function rather than a mocked urllib so a
# test drives the real code path down to the byte payload, and so the only
# thing faked is the network — which is the only thing a container cannot have.
def _http(method: str, path: str, body: Optional[bytes] = None,
          timeout: float = 30.0) -> bytes:
    base = str(getattr(config, "RELAY_URL", "") or "").rstrip("/")
    if not base:
        raise RelayError("RELAY_URL is not set.")
    token = str(getattr(config, "RELAY_TOKEN", "") or "")
    if not token:
        raise RelayError(
            "RELAY_TOKEN is not set. An unauthenticated relay would accept a "
            "snapshot from anyone who found the URL.")
    req = urllib.request.Request(
        f"{base}{path}", data=body, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RelayError(f"relay returned {e.code} for {method} {path}")
    except Exception as e:
        raise RelayError(f"could not reach the relay: {type(e).__name__}: {e}")


# ── state, so a failing relay is never silent ────────────────────────────────

def _fresh_last() -> dict:
    """One definition of the shape of `_last`.

    It was a dict literal here and a second literal in every test fixture that
    reset it. Adding two keys for the context push broke five tests with
    KeyError — not because the behaviour changed, but because the shape was
    written down in six places. Tests now call this, so the next field costs
    nothing.
    """
    return {"pushed_at": 0.0, "bytes": 0, "error": "", "attempted_at": 0.0,
            "context_at": 0.0, "context_chars": 0, "context_error": ""}


_last: dict = _fresh_last()

import threading as _threading
_stop = _threading.Event()


def enabled() -> bool:
    return bool(getattr(config, "RELAY_ENABLED", False))


def push_snapshot() -> dict:
    """Seal the brain and send it. Returns what happened; never raises upward.

    The result is recorded either way. A relay that has been unreachable for a
    week is not an error anyone sees at the moment it happens — it is a stale
    snapshot the phone reads later and believes. So the failure has to be
    durable enough to be asked about, which `status()` is for.
    """
    _last["attempted_at"] = time.time()
    if not enabled():
        _last["error"] = ""
        return {"ok": False, "skipped": "RELAY_ENABLED is false"}
    try:
        blob = seal(snapshot_bytes())
        _http("PUT", "/snapshot", blob)
    except RelayError as e:
        _last["error"] = str(e)
        print(f"[Relay] Snapshot NOT sent: {e}")
        return {"ok": False, "error": str(e)}
    _last.update({"pushed_at": time.time(), "bytes": len(blob), "error": ""})
    return {"ok": True, "bytes": len(blob)}


def push_context() -> dict:
    """Send the scoped, readable slice the cloud reasons over.

    Distinct from the snapshot in every way that matters. The snapshot is the
    whole brain, sealed, and the relay cannot open it. This is a page of text
    the relay CAN read, because a box that cannot read cannot answer — which is
    the cost §3a of the plan accepted, out loud, and this is where it is paid.

    Built from an allowlist of sources in agent/working_context.py, redacted,
    and bounded.
    """
    if not enabled():
        return {"ok": False, "skipped": "RELAY_ENABLED is false"}
    from agent import working_context
    try:
        ctx = working_context.build()
        body = json.dumps({"context": ctx,
                           "tier": working_context.cloud_tier()}).encode()
        _http("PUT", "/context", body)
    except RelayError as e:
        _last["context_error"] = str(e)
        print(f"[Relay] Context NOT sent: {e}")
        return {"ok": False, "error": str(e)}
    _last["context_at"] = time.time()
    _last["context_chars"] = ctx["chars"]
    _last["context_error"] = ""
    return {"ok": True, "chars": ctx["chars"], "sources": ctx["sources"],
            "errors": ctx["errors"]}


def pull_snapshot() -> bytes:
    """Fetch and open the last snapshot. Raises if it will not decrypt."""
    return unseal(_http("GET", "/snapshot"))


def status() -> dict:
    """Four states, because they need four different fixes and one boolean
    would flatten them into "no relay" — the same reasoning `mcp_client.status`
    already uses."""
    if not enabled():
        state, detail = "off", ("RELAY_ENABLED is false. Nothing is sent "
                                "anywhere and the phone sees only what it can "
                                "reach directly.")
    elif not getattr(config, "RELAY_URL", ""):
        state, detail = "unconfigured", "RELAY_ENABLED is true but RELAY_URL is empty."
    elif _last.get("error"):
        state, detail = "failing", (
            f"The last attempt failed: {_last.get('error', '')} Whatever the phone reads "
            f"is from {_ago(_last.get('pushed_at', 0.0))}.")
    elif not _last.get("pushed_at", 0.0):
        state, detail = "never_pushed", (
            "Configured, but no snapshot has been sent in this process yet.")
    else:
        state, detail = "ok", f"Last snapshot {_ago(_last.get('pushed_at', 0.0))}."
    return {"state": state, "detail": detail,
            "pushed_at": _last.get("pushed_at", 0.0), "bytes": _last.get("bytes", 0),
            "attempted_at": _last.get("attempted_at", 0.0), "error": _last.get("error", ""),
            "context_at": _last.get("context_at", 0.0),
            "context_chars": _last.get("context_chars", 0),
            "context_error": _last.get("context_error", ""),
            "context_note": ("The snapshot is sealed and the relay cannot read "
                             "it. The context is readable there — that is what "
                             "lets it answer while the laptop is off."),
            "url": str(getattr(config, "RELAY_URL", "") or "")}


# ── the outbox: work that arrived while the laptop was off ───────────────────
#
# The success check is "a message queued while offline is applied exactly once
# when the laptop returns", and the honest version of that sentence needs two
# qualifications written down rather than discovered.
#
# **Dedupe is on the item's own id, not the relay's row id.** The sender mints a
# uuid inside the sealed payload. A relay operator cannot forge an item — they
# have no key — but they can replay one they are already storing, and a replay
# under a fresh row id would otherwise be a second, legitimate-looking delivery.
# The id that decides "have I seen this" therefore has to be one they cannot
# choose.
#
# **Exactly-once is not achievable across two systems that do not share a
# transaction.** The claim is written here, the effect happens here, the
# acknowledgement happens over there, and a crash can land between any two of
# them. What this does is claim, apply, then acknowledge — and a row left
# `in_progress` by a crash is RETRIED on the next drain rather than abandoned,
# because a duplicated note is visible and harmless while a silently dropped one
# is neither. `attempts` is recorded so a retry loop cannot hide.

APPLY_MAX_ATTEMPTS = 3


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS relay_applied (
                item_id    TEXT PRIMARY KEY,
                relay_id   INTEGER NOT NULL DEFAULT 0,
                kind       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'in_progress',
                attempts   INTEGER NOT NULL DEFAULT 0,
                first_seen REAL NOT NULL,
                applied_at REAL NOT NULL DEFAULT 0,
                error      TEXT NOT NULL DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_relay_applied_status "
                  "ON relay_applied(status)")


def new_item(kind: str, **payload) -> dict:
    """One outbox item, with the id that dedupe will key on."""
    import uuid
    return {"id": uuid.uuid4().hex, "kind": str(kind), "ts": time.time(),
            "payload": payload}


def queue(kind: str, **payload) -> dict:
    """Seal an item and leave it on the relay for the laptop to pick up."""
    if not enabled():
        return {"ok": False, "skipped": "RELAY_ENABLED is false"}
    item = new_item(kind, **payload)
    try:
        blob = seal(json.dumps(item).encode())
        _http("POST", "/outbox", blob)
    except RelayError as e:
        print(f"[Relay] Could not queue a {kind}: {e}")
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": item["id"]}


def pending() -> list[dict]:
    """Unsealed outbox items, oldest first.

    An item that will not decrypt is REPORTED rather than dropped. It means the
    key changed or the relay returned something other than what was stored, and
    both are worth knowing; skipping quietly would turn either into "you have no
    messages".
    """
    import base64
    raw = json.loads(_http("GET", "/outbox").decode() or "{}")
    out = []
    for row in raw.get("items", []):
        try:
            item = json.loads(unseal(base64.b64decode(row["ciphertext_b64"])))
        except Exception as e:
            print(f"[Relay] Outbox item {row.get('id')} would not open: {e}")
            out.append({"relay_id": row.get("id"), "unreadable": str(e)})
            continue
        item["relay_id"] = row.get("id")
        out.append(item)
    return out


def _apply_note(payload: dict) -> str:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RelayError("a note with no text")
    return longterm.remember(text, kind=str(payload.get("kind") or "note"),
                             importance=int(payload.get("importance") or 5))


# Deny-by-default, for the same reason ROLE_TOOLS and MCP_ALLOW are. Items are
# sealed and therefore unforgeable, but an older laptop meeting a kind a newer
# phone invented must refuse it rather than improvise — and "we had not thought
# of that kind yet" has never been a reason to run something.
APPLY: dict = {"note": _apply_note}


def drain() -> dict:
    """Pull, apply, acknowledge. Returns a summary; never raises upward."""
    if not enabled():
        return {"ok": False, "skipped": "RELAY_ENABLED is false"}
    try:
        items = pending()
    except RelayError as e:
        _last["error"] = str(e)
        print(f"[Relay] Could not read the outbox: {e}")
        return {"ok": False, "error": str(e)}

    applied = skipped = failed = 0
    for item in items:
        if item.get("unreadable"):
            failed += 1
            continue
        item_id, kind = str(item.get("id") or ""), str(item.get("kind") or "")
        relay_id = item.get("relay_id")
        if not item_id:
            failed += 1
            print("[Relay] An outbox item had no id; refusing to apply it "
                  "because there would be no way to avoid applying it again.")
            continue

        if not _claim(item_id, relay_id, kind):
            skipped += 1
            _ack(relay_id)          # already applied; the ack simply never landed
            continue

        handler = APPLY.get(kind)
        if handler is None:
            _finish(item_id, "unsupported",
                    error=f"no handler for kind '{kind}'")
            failed += 1
            print(f"[Relay] Outbox item {item_id[:8]} has kind '{kind}', which "
                  f"this version does not know how to apply. Left unapplied "
                  f"rather than guessed at.")
            continue
        try:
            handler(item.get("payload") or {})
        except Exception as e:
            _finish(item_id, "failed", error=f"{type(e).__name__}: {e}")
            failed += 1
            print(f"[Relay] Outbox item {item_id[:8]} failed: {e}")
            continue
        _finish(item_id, "applied")
        _ack(relay_id)
        applied += 1

    return {"ok": True, "applied": applied, "skipped": skipped, "failed": failed,
            "seen": len(items)}


# ── replies the cloud wrote while the laptop was off ─────────────────────────
#
# A reply is DATA, never an instruction. It was written on a machine you may not
# own, by a model, from a context that machine could read. If the relay were
# compromised an attacker could put "run rm -rf /" in one — so replies are filed
# as content and their `requests` become QUEUED TASKS, which then go through
# safety.check, mcp_policy.enforce and subagent_scope.check on the laptop like
# anything else.
#
# The worst a hostile relay achieves is a wrong answer and a task sitting
# visibly in the queue.

REPLY_MAX_REQUESTS = 5


def pull_replies() -> list[dict]:
    raw = json.loads(_http("GET", "/replies").decode() or "{}")
    return raw.get("items", []) or []


def drain_replies() -> dict:
    """File what the cloud answered. Exactly once, as content, never as commands."""
    if not enabled():
        return {"ok": False, "skipped": "RELAY_ENABLED is false"}
    try:
        items = pull_replies()
    except RelayError as e:
        print(f"[Relay] Could not read replies: {e}")
        return {"ok": False, "error": str(e)}

    filed = queued = skipped = 0
    for item in items:
        relay_id = item.get("id")
        # Dedupe on the relay's row id here, unlike the outbox. These are not
        # sealed and not replayable by a third party: the only writer is the
        # answerer on the same box, and its ids come from one AUTOINCREMENT.
        # The threat the outbox's inner id defends against — a stored item put
        # back under a fresh id — is the same actor either way here, and it
        # gains nothing it could not do by writing a new reply.
        item_id = f"reply:{relay_id}"
        if not _claim(item_id, relay_id, "reply"):
            skipped += 1
            _ack_reply(relay_id)
            continue

        answer = str(item.get("answer") or "").strip()
        question = str(item.get("question") or "").strip()
        if answer:
            try:
                longterm.remember(
                    f"[Answered from the relay while the laptop was off]\n"
                    f"Q: {question}\nA: {answer}"[:4000],
                    kind="note", importance=4)
                filed += 1
            except Exception as e:
                _finish(item_id, "failed", error=f"{type(e).__name__}: {e}")
                continue

        queued += _queue_requests(item.get("requests"))
        _finish(item_id, "applied")
        _ack_reply(relay_id)

    return {"ok": True, "filed": filed, "queued": queued, "skipped": skipped,
            "seen": len(items)}


def _queue_requests(requests) -> int:
    """Turn what the cloud ASKED FOR into tasks the laptop decides about.

    Never executed here. `node_tasks.submit` records a request, and
    `agent/node_worker.py` runs it through the local gates — or refuses it,
    which is a normal outcome.
    """
    if isinstance(requests, str):
        try:
            requests = json.loads(requests)
        except Exception:
            return 0
    if not isinstance(requests, list):
        return 0
    from agent import node_tasks
    n = 0
    for req in requests[:REPLY_MAX_REQUESTS]:
        if not isinstance(req, dict):
            continue
        tool = str(req.get("tool") or "").strip()
        if not tool:
            continue
        try:
            node_tasks.submit("tool", payload={
                "name": tool, "inputs": req.get("inputs") or {},
                "asked_by": "relay", "why": str(req.get("why") or "")[:500]})
            n += 1
        except Exception as e:
            print(f"[Relay] Could not queue '{tool}' from a reply: {e}")
    return n


def _ack_reply(relay_id) -> None:
    if not relay_id:
        return
    try:
        _http("POST", f"/replies/{int(relay_id)}/done", b"")
    except RelayError as e:
        print(f"[Relay] Filed reply {relay_id} but could not acknowledge it: {e}")


def _claim(item_id: str, relay_id, kind: str) -> bool:
    """True if this process should apply the item now.

    The INSERT is the lock: a PRIMARY KEY collision is how a second drain — or
    a second Apex — finds out someone else already has it, with no read-then-
    write race in between.

    A row left `in_progress` by a crash is re-claimed, up to APPLY_MAX_ATTEMPTS.
    That trades a possible duplicate for a guaranteed delivery, which is the
    right way round when the effect is "remember this": a duplicate note is
    visible and harmless, a dropped one is neither.
    """
    now = time.time()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO relay_applied "
            "(item_id, relay_id, kind, status, attempts, first_seen) "
            "VALUES (?, ?, ?, 'in_progress', 1, ?)",
            (item_id, int(relay_id or 0), kind, now))
        if cur.rowcount:
            c.commit()
            return True
        row = c.execute("SELECT status, attempts FROM relay_applied "
                        "WHERE item_id = ?", (item_id,)).fetchone()
        if not row or row[0] != "in_progress" or row[1] >= APPLY_MAX_ATTEMPTS:
            return False
        c.execute("UPDATE relay_applied SET attempts = attempts + 1 "
                  "WHERE item_id = ?", (item_id,))
        c.commit()
        print(f"[Relay] Retrying outbox item {item_id[:8]} — a previous attempt "
              f"claimed it and did not finish (attempt {row[1] + 1}).")
        return True


def _finish(item_id: str, status: str, error: str = "") -> None:
    with longterm._conn() as c:
        c.execute("UPDATE relay_applied SET status = ?, applied_at = ?, error = ? "
                  "WHERE item_id = ?", (status, time.time(), error[:500], item_id))
        c.commit()


def _ack(relay_id) -> None:
    """Tell the relay it can stop offering the item. Best effort: a failure here
    leaves it pending, and the next drain skips it on the dedupe table and acks
    again."""
    if not relay_id:
        return
    try:
        _http("POST", f"/outbox/{int(relay_id)}/done", b"")
    except RelayError as e:
        print(f"[Relay] Applied item {relay_id} but could not acknowledge it: {e}")


# ── running it ───────────────────────────────────────────────────────────────

_thread = None


def start_background() -> str:
    """Drain and snapshot on a timer. Returns the line to print at boot.

    Always says what it decided, including when it decided to do nothing. A
    subsystem that is configured, constructed and silently never runs is
    indistinguishable from one that is working, and this codebase has produced
    that shape often enough that "off" has to be as loud as "on".
    """
    global _thread
    if not enabled():
        return "[Relay] Off — nothing is sent anywhere (RELAY_ENABLED=false)."
    if not getattr(config, "RELAY_URL", ""):
        return "[Relay] Enabled but RELAY_URL is empty, so nothing will be sent."
    if _thread is not None and _thread.is_alive():
        return "[Relay] Already running."
    try:
        _fernet()                       # fail at boot, not at the first push
    except RelayError as e:
        return f"[Relay] NOT started: {e}"

    import threading
    minutes = max(1, int(getattr(config, "RELAY_SNAPSHOT_MINUTES", 30)))

    def loop():
        # Drain first, and before the first sleep. Work that arrived while the
        # laptop was off is the thing someone is actually waiting on; making
        # them wait another half hour for it would miss the point of the phase.
        while True:
            try:
                drain()
            except Exception as e:
                print(f"[Relay] drain error: {e}")
            try:
                push_snapshot()
            except Exception as e:
                print(f"[Relay] snapshot error: {e}")
            try:
                push_context()
            except Exception as e:
                print(f"[Relay] context error: {e}")
            try:
                drain_replies()
            except Exception as e:
                print(f"[Relay] reply error: {e}")
            if _stop.wait(timeout=minutes * 60):
                return

    _stop.clear()
    _thread = threading.Thread(target=loop, daemon=True, name="Relay")
    _thread.start()
    return (f"[Relay] Watching {config.RELAY_URL} — draining and snapshotting "
            f"every {minutes}m.")


def stop_background() -> None:
    _stop.set()


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    secs = max(0, int(time.time() - ts))
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


if __name__ == "__main__":
    import sys
    if "--new-key" in sys.argv:
        print(new_key())
        print("\nPut this in .env as RELAY_KEY. Never give it to the relay, and "
              "never commit it — a snapshot sealed with a lost key is lost.")
    else:
        print(json.dumps(status(), indent=2))
