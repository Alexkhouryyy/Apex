"""What a node can actually do — established by testing, never by being told.

Step 4 of `docs/PHASE_6_7_PLAN.md`. `agent/devices.py` has always known *what*
is connected; this is what each one can *do*, which is the question a
dispatcher has to answer before it queues work for a machine.

## Why probing rather than declaring

This is `agent/mcp_policy.py`'s asymmetry one layer out. A node reporting "I can
run Blender" is a claim by the party the claim is about, and a dispatcher that
believes it queues work that can never run — where it sits in the queue looking
in-progress, which is worse than a refusal because nothing is obviously wrong.

So there is deliberately **no function here that accepts a capability and a
boolean from a caller**, and no route that sets one. `refresh()` runs the probes
on the machine they describe and writes what they returned. That absence is the
enforcement; `tests/test_capabilities.py` asserts it.

## Three states, because they need three different fixes

  `yes`      the probe ran and succeeded
  `no`       the probe ran and the thing is not there
  `unknown`  the probe itself failed — we do not know

`unknown` is deliberately not folded into `no`. For dispatch they behave the
same (`can()` is False for both, deny-by-default), but "Blender is not
installed" and "the Blender probe crashed" are different problems, and one
boolean would make them the same sentence on the dashboard.

## The trap in probing a camera

The obvious probe is `cv2.VideoCapture(0)` — and it would be a real bug. The
webcam is exclusive: opening it takes it away from `agent/handtrack.py`, so a
capability check running on a timer would fight the hand tracker for the device
and each would intermittently find it busy. The probe therefore asks whether
the *software* is usable (`handtrack.available()` — imports, model file) and
treats a tracker already holding the camera as proof rather than as a conflict.

A probe with a side effect is not a probe.
"""
from __future__ import annotations

import time
from typing import Callable, NamedTuple

import config
from agent import longterm

YES, NO, UNKNOWN = "yes", "no", "unknown"

# Beyond this a capability is reported `stale` and treated as unusable. A
# laptop that had Blender in March and does not in September should not be
# handed a modelling task on the strength of a six-month-old probe.
MAX_AGE_SECONDS = 6 * 3600


class Probe(NamedTuple):
    state: str
    detail: str


def init_db() -> None:
    with longterm._conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS device_capabilities (
                device_id   TEXT NOT NULL,
                name        TEXT NOT NULL,
                state       TEXT NOT NULL,
                detail      TEXT NOT NULL DEFAULT '',
                verified_at REAL NOT NULL,
                PRIMARY KEY (device_id, name)
            )
        """)


# ── the probes ───────────────────────────────────────────────────────────────

def _probe_shell() -> Probe:
    """Can this node start a process at all? Containers and locked-down hosts
    cannot, and finding that out when a delegated command silently returns
    nothing is the wrong time."""
    import subprocess
    import sys
    try:
        r = subprocess.run([sys.executable, "-c", "pass"], timeout=15,
                           capture_output=True)
    except Exception as e:
        return Probe(UNKNOWN, f"could not test: {type(e).__name__}: {e}")
    return (Probe(YES, "processes can be started") if r.returncode == 0
            else Probe(NO, f"a trivial subprocess exited {r.returncode}"))


def _probe_files() -> Probe:
    """Writable working storage, tested by writing. `os.access` lies on
    Windows, on network shares, and on a full disk."""
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(prefix="apex_cap_", delete=True) as f:
            f.write(b"probe")
            f.flush()
    except Exception as e:
        return Probe(NO, f"cannot write a temporary file: {type(e).__name__}: {e}")
    return Probe(YES, "temporary storage is writable")


def _probe_camera() -> Probe:
    """Deliberately does NOT open the device — see the module docstring.

    The first version of this returned YES from `handtrack.available()` alone,
    and said YES on the build container, which has mediapipe installed and no
    camera at all. That is precisely the answer this whole module exists to
    prevent: a dispatcher would have queued a camera task to a machine with
    nothing to point at it.

    `available()` answers "is the software usable", which is a necessary and
    nowhere near sufficient condition. So the evidence, in order of how much it
    proves:

      1. A tracker is running and has the camera open — direct, and free.
      2. A tracker is running and reported the camera would not open — also
         direct, and the reason is worth passing on.
      3. No tracker, and the platform exposes device nodes we can list without
         opening them (Linux `/dev/video*`).
      4. No tracker, and no cheap way to look — UNKNOWN, honestly. Not YES.
         Nothing has tried the device, so nobody knows, and `unknown` exists as
         a separate state exactly so this case does not have to lie either way.
    """
    import glob
    import sys
    try:
        from agent import handtrack
    except Exception as e:
        return Probe(UNKNOWN, f"handtrack would not import: {type(e).__name__}: {e}")
    try:
        tracker = handtrack.active_tracker()
        if tracker is not None:
            if getattr(tracker, "_cap", None) is not None:
                return Probe(YES, "a tracker has the camera open right now")
            if getattr(tracker, "_reported", "") == "camera_busy":
                return Probe(NO, "the tracker is running and the camera "
                                 "would not open")
        ok, why = handtrack.available()
    except Exception as e:
        return Probe(UNKNOWN, f"the probe failed: {type(e).__name__}: {e}")
    if not ok:
        return Probe(NO, why)
    if sys.platform.startswith("linux"):
        nodes = glob.glob("/dev/video*")
        return (Probe(YES, f"camera software is usable and {len(nodes)} video "
                           f"device(s) are present")
                if nodes else
                Probe(NO, "camera software is usable but there is no video "
                          "device on this machine"))
    return Probe(UNKNOWN, "camera software is usable, but nothing has opened "
                          "the device and it cannot be checked here without "
                          "taking it away from hand tracking")


def _probe_blender() -> Probe:
    """A real ping to the addon socket. `BLENDER_ENABLED` alone proves nothing —
    the flag says someone intended it, not that Blender is running."""
    if not getattr(config, "BLENDER_ENABLED", False):
        return Probe(NO, "BLENDER_ENABLED is false")
    try:
        from agent import blender_bridge
        return (Probe(YES, "the addon answered a ping")
                if blender_bridge.available()
                else Probe(NO, "Blender is not running, or the addon is not installed"))
    except Exception as e:
        return Probe(UNKNOWN, f"the probe failed: {type(e).__name__}: {e}")


def _probe_local_model() -> Probe:
    """Ollama, asked whether it has any models — not merely whether something
    answers the port. An Ollama with nothing pulled can serve no request, and
    reporting it as available is how a delegated job fails at the far end."""
    import json
    import urllib.request
    base = str(getattr(config, "OLLAMA_BASE_URL", "") or "").rstrip("/")
    if not base:
        return Probe(NO, "OLLAMA_BASE_URL is not set")
    url = base[:-3] + "/api/tags" if base.endswith("/v1") else base + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            models = json.loads(r.read() or b"{}").get("models") or []
    except Exception as e:
        return Probe(NO, f"no local model server at {base}: {type(e).__name__}")
    if not models:
        return Probe(NO, f"{base} is running but has no models pulled")
    return Probe(YES, f"{len(models)} local model(s) available")


# GPU is deliberately absent. The only probe available would be to build a
# MediaPipe landmarker and read back which delegate it chose, which costs
# hundreds of milliseconds and loads a model — and a capability that cannot be
# checked cheaply and honestly is better left out than guessed at. Nothing
# dispatches on it, so its absence costs nothing today.
PROBES: dict[str, Callable[[], Probe]] = {
    "shell": _probe_shell,
    "files": _probe_files,
    "camera": _probe_camera,
    "blender": _probe_blender,
    "local_model": _probe_local_model,
}


# ── running them, and reading the results ────────────────────────────────────

def probe_all() -> dict[str, Probe]:
    """Run every probe. A probe that raises becomes `unknown` rather than
    taking the sweep down with it — one broken check must not leave a node with
    no capabilities recorded at all, which reads identically to a node that can
    do nothing."""
    out = {}
    for name, fn in PROBES.items():
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = Probe(UNKNOWN, f"probe raised: {type(e).__name__}: {e}")
    return out


def refresh(device_id: str) -> dict[str, Probe]:
    """Probe this machine and record the results against `device_id`.

    The only way anything gets written. There is no setter that takes a name
    and a boolean, on purpose — see the module docstring.
    """
    if not device_id:
        raise ValueError("refresh() needs a device id")
    results = probe_all()
    now = time.time()
    with longterm._conn() as c:
        for name, p in results.items():
            c.execute(
                "INSERT INTO device_capabilities (device_id, name, state, detail,"
                " verified_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(device_id, name) DO UPDATE SET"
                " state=excluded.state, detail=excluded.detail,"
                " verified_at=excluded.verified_at",
                (device_id, name, p.state, p.detail[:300], now))
        c.commit()
    return results


def of(device_id: str, *, now: float | None = None) -> dict[str, dict]:
    """Everything recorded for one node, with staleness resolved."""
    now = time.time() if now is None else now
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT name, state, detail, verified_at FROM device_capabilities"
            " WHERE device_id = ? ORDER BY name", (device_id,)).fetchall()
    out = {}
    for name, state, detail, ts in rows:
        stale = (now - ts) > MAX_AGE_SECONDS
        out[name] = {"state": state, "detail": detail, "verified_at": ts,
                     "stale": stale, "usable": state == YES and not stale}
    return out


def can(device_id: str, name: str, *, now: float | None = None) -> bool:
    """Deny-by-default. False for `no`, for `unknown`, for stale, and for a
    capability that was never probed — a node cannot acquire an ability by
    having not been asked about it."""
    return bool(of(device_id, now=now).get(name, {}).get("usable"))


def this_node() -> str:
    """A stable id for the machine Apex is running on.

    The hostname, deliberately: it is stable across restarts, means something to
    a person reading the dashboard, and needs nothing persisted to mint it. Two
    machines on one account with the same hostname would collide — a real
    limitation, and not one this system has, since the whole design has exactly
    one laptop as its author.
    """
    import platform
    return (platform.node() or "unknown-node").strip()


def refresh_this_node() -> str:
    """Probe this machine at boot and say what came back.

    Returned as a line to print rather than printed here, so the caller decides
    where it goes — `main.py` prints, `app/resident.py` logs. Always says
    something: a capability sweep that ran and found nothing worth mentioning is
    indistinguishable from one that never ran.
    """
    node = this_node()
    try:
        results = refresh(node)
    except Exception as e:
        return f"[Capabilities] Probe failed on {node}: {type(e).__name__}: {e}"
    yes = sorted(n for n, p in results.items() if p.state == YES)
    other = sorted(f"{n}({p.state})" for n, p in results.items() if p.state != YES)
    return (f"[Capabilities] {node} can: {', '.join(yes) or 'nothing'}"
            + (f" — not: {', '.join(other)}" if other else ""))


def summary(*, now: float | None = None) -> list[dict]:
    """Every node and what it can do, for the dashboard. Nodes whose probes
    have gone stale still appear — a node that vanished is a fact, and dropping
    it from the list would make it look like it was never there."""
    now = time.time() if now is None else now
    with longterm._conn() as c:
        ids = [r[0] for r in c.execute(
            "SELECT DISTINCT device_id FROM device_capabilities ORDER BY device_id")]
    return [{"device_id": d, "capabilities": of(d, now=now)} for d in ids]
