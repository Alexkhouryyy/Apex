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

_last: dict = {"pushed_at": 0.0, "bytes": 0, "error": "", "attempted_at": 0.0}


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
    elif _last["error"]:
        state, detail = "failing", (
            f"The last attempt failed: {_last['error']} Whatever the phone reads "
            f"is from {_ago(_last['pushed_at'])}.")
    elif not _last["pushed_at"]:
        state, detail = "never_pushed", (
            "Configured, but no snapshot has been sent in this process yet.")
    else:
        state, detail = "ok", f"Last snapshot {_ago(_last['pushed_at'])}."
    return {"state": state, "detail": detail,
            "pushed_at": _last["pushed_at"], "bytes": _last["bytes"],
            "attempted_at": _last["attempted_at"], "error": _last["error"],
            "url": str(getattr(config, "RELAY_URL", "") or "")}


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
