"""The barehands board — Apex's face and its hands on the glass.

The outbound half of the barehands bridge. `agent/barehands_watcher.py` is the
inbound half; between them they are the only things in Apex that know port 8794
exists. Nothing of barehands is vendored — this speaks HTTP to localhost and
writes three small files, which is aggregation, so Apex stays MIT.

Two protocols, both barehands' own:

  * **The ring is a face.** Writing one word of `idle|listening|thinking|
    speaking` into `<repo>/state/state` moves it. `app/resident.py`'s
    `ResidentState` already uses those exact four words, so publishing Apex's
    real state is a listener registration rather than a new mechanism.
  * **The board is a stage.** `POST /cmd` with a server-side action allowlist,
    and any `src` jailed to barehands' own `media/` folder.

## The lie this module exists to stop

`POST /cmd` returns **204 with no tracker connected**. barehands queues the
command in `_CMDS` and hands it out on the tracker's next heartbeat, so with no
Chrome tab open the card is queued into the void and the HTTP call still looks
like a success. Verified against a running server:

    $ curl -o /dev/null -w '%{http_code}' -X POST .../cmd -d '{"a":"add_card"...}'
    204
    $ curl .../state
    {}

So every command reports which of four conditions it actually met, rather than
reporting 204 and letting the user wonder why nothing appeared.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import config
from agent.barehands_watcher import (
    TRACKER_DOWN, TRACKER_FROZEN, TRACKER_LIVE, TRACKER_NO_STAGE,
)

# barehands' own allowlist, mirrored so a typo fails here with a readable
# message instead of arriving as an opaque 400.
ALLOWED_ACTIONS = (
    "add_img", "add_card", "clear", "reset", "hand", "give", "yank", "hover",
    "scroll_note", "widget", "explode", "assemble", "present",
)

RING_STATES = ("idle", "listening", "thinking", "speaking")

# barehands' _CMDS queue is unbounded and only drains when a tracker is pushing,
# so Apex limits its own send rate rather than piling commands into a queue
# nobody is reading.
MIN_SEND_INTERVAL = 0.25
_last_send = 0.0
_send_lock = threading.Lock()

# The watcher, when running, is the only thing that can tell live from frozen —
# that needs two samples over time, which a one-shot tool call does not have.
_tracker_probe: Optional[Callable[[], str]] = None


def set_tracker_probe(fn: Optional[Callable[[], str]]) -> None:
    """Let the watcher answer 'is the stage actually live?' on our behalf."""
    global _tracker_probe
    _tracker_probe = fn


def _base() -> str:
    return (getattr(config, "BAREHANDS_URL", "") or "http://127.0.0.1:8794").rstrip("/")


def is_configured() -> bool:
    return bool(getattr(config, "BAREHANDS_ENABLED", False))


def state_dir() -> Optional[Path]:
    """The ring's runtime folder, or None if BAREHANDS_DIR isn't usable.

    Checked rather than assumed: a wrong path makes every write raise into a
    `try/except`, and the ring simply never moves with nothing printed.
    """
    raw = (getattr(config, "BAREHANDS_DIR", "") or "").strip()
    if not raw:
        return None
    d = Path(raw).expanduser() / "state"
    return d if d.is_dir() else None


def _get(path: str, timeout: float = 2.0) -> Optional[bytes]:
    try:
        req = urllib.request.Request(f"{_base()}{path}", headers={"User-Agent": "Apex"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def tracker_status() -> str:
    """Which of the four conditions the board is in right now."""
    if _tracker_probe is not None:
        try:
            return _tracker_probe()
        except Exception:
            pass
    body = _get("/state")
    if body is None:
        return TRACKER_DOWN
    try:
        data = json.loads(body)
    except Exception:
        return TRACKER_DOWN
    if not isinstance(data, dict) or ("cursors" not in data and "items" not in data):
        return TRACKER_NO_STAGE
    # Without the watcher there is no way to tell a live static board from a
    # frozen one — a settled board legitimately pushes identical bytes — so the
    # honest answer is the weaker one.
    return TRACKER_LIVE


# ── The ring (Apex's face) ───────────────────────────────────────────────────

def publish_state(state: str) -> str:
    """Write one of barehands' four words into <repo>/state/state."""
    if not is_configured():
        return "[Barehands] disabled (BAREHANDS_ENABLED=false)."
    d = state_dir()
    if d is None:
        return ("[Barehands] BAREHANDS_DIR is not set to a barehands checkout "
                "with a state/ folder — the ring cannot be driven.")
    word = (state or "").strip().lower()
    # ResidentState.MUTED has no barehands equivalent; it shows as a quiet ring
    # with an amber mood rather than being dropped on the floor.
    if word == "muted":
        publish_mood("amber")
        word = "idle"
    if word not in RING_STATES:
        return f"[Barehands] '{state}' is not one of {', '.join(RING_STATES)}."
    try:
        (d / "state").write_text(word)
    except Exception as e:
        return f"[Barehands] could not write the ring state: {e}"
    return f"[Barehands] ring → {word}"


def publish_mood(mood: str) -> str:
    """Set the ring's colour. barehands ignores a mood older than 45 s, so this
    is a heartbeat rather than a latch."""
    d = state_dir()
    if d is None:
        return "[Barehands] BAREHANDS_DIR not configured."
    if mood not in ("green", "amber", "red"):
        return f"[Barehands] '{mood}' is not green, amber or red."
    try:
        (d / "mood.json").write_text(json.dumps({"mood": mood, "ts": time.time()}))
    except Exception as e:
        return f"[Barehands] could not write the ring mood: {e}"
    return f"[Barehands] mood → {mood}"


def attach_to_resident_state(state) -> str:
    """Mirror a ResidentState onto the ring.

    `set()` fires listeners only on a CHANGE and never carries MUTED (mute lives
    outside the state machine and is applied in `get()`), so the listener reads
    `state.get()` rather than trusting its argument — and the current state is
    published once here, or the ring would sit on a stale file until Apex next
    happened to change state.
    """
    if not is_configured():
        return "[Barehands] disabled."
    if state_dir() is None:
        return ("[Barehands] BAREHANDS_DIR is not a barehands checkout — the "
                "ring will not follow Apex.")

    def _publish(_new_state: str) -> None:
        publish_state(state.get())

    state.add_listener(_publish)
    publish_state(state.get())
    return "[Barehands] the ring now follows Apex."


# ── The board (Apex's hands) ─────────────────────────────────────────────────

def board_command(cmd: dict) -> str:
    """POST one command to the board, and report what actually happened.

    A bare 204 is not the truth: barehands queues commands for a tracker that
    may not exist. The four conditions get four different answers.
    """
    global _last_send
    if not is_configured():
        return "[Barehands] disabled (BAREHANDS_ENABLED=false)."
    action = (cmd or {}).get("a")
    if action not in ALLOWED_ACTIONS:
        return (f"[Barehands] '{action}' is not a board action. "
                f"Allowed: {', '.join(ALLOWED_ACTIONS)}.")

    with _send_lock:
        wait = MIN_SEND_INTERVAL - (time.time() - _last_send)
        if wait > 0:
            time.sleep(wait)
        _last_send = time.time()

    status = tracker_status()
    if status == TRACKER_DOWN:
        return "[Barehands] the board is dark — the barehands server isn't running."

    try:
        req = urllib.request.Request(
            f"{_base()}/cmd", data=json.dumps(cmd).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Apex"},
            method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return ("[Barehands] the board refused that command — check the "
                    "action, and remember any src must already be inside "
                    "barehands' media/ folder.")
        return f"[Barehands] board error {e.code}."
    except Exception as e:
        return f"[Barehands] could not reach the board: {e}"

    if code != 204:
        return f"[Barehands] unexpected board response {code}."
    if status == TRACKER_NO_STAGE:
        return (f"[Barehands] '{action}' queued, but no stage is open — nothing "
                f"will appear until stage.html is open in Chrome.")
    if status == TRACKER_FROZEN:
        return (f"[Barehands] '{action}' queued, but the stage is frozen — that "
                f"Chrome tab isn't in front, so it won't render until it is.")
    return f"[Barehands] '{action}' is on the board."


# What the airlock will stage, mirrored from barehands' server so a wrong
# extension fails here with a readable message instead of an opaque 400.
PROP_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm", ".glb", ".gltf")

# The airlock's folders, and what each one means on the board.
_PROP_FOLDERS = {
    "misc": "images",
    "fx": "transparent props (no frame)",
    "models": "3D models, solid",
    "holo": "3D models, rendered as a blue hologram wireframe",
}


def list_props() -> list:
    """Every file the board is allowed to stage, as airlock-relative paths.

    barehands jails staging to its own `media/` folder — nothing outside it can
    ever appear, which is a safety property rather than an inconvenience. So the
    only useful question is what is already inside, and `/props` answers it from
    the live filesystem: drop a file in, and it is listed without a restart.
    """
    body = _get("/props")
    if body is None:
        return []
    try:
        tree = json.loads(body)
    except Exception:
        return []

    out: list = []

    def walk(node, prefix=""):
        if not isinstance(node, dict):
            return
        for item in node.get("items") or []:
            out.append(str(item).replace("\\", "/"))
        for sub in node.get("dirs") or []:
            walk(sub, prefix)

    walk(tree)
    return sorted(out)


def describe_props() -> str:
    """The airlock as something a model can read and choose from."""
    if not is_configured():
        return "[Barehands] disabled (BAREHANDS_ENABLED=false)."
    props = list_props()
    if not props:
        status = tracker_status()
        if status == TRACKER_DOWN:
            return "[Barehands] the board is dark — the barehands server isn't running."
        return ("The props airlock is empty. Drop files into barehands' media/ "
                "folder: " + "; ".join(f"media/{k}/ = {v}"
                                       for k, v in _PROP_FOLDERS.items()))
    lines = [f"{len(props)} prop(s) the board can stage:"]
    lines += [f"  {p}" for p in props[:60]]
    if len(props) > 60:
        lines.append(f"  … and {len(props) - 60} more")
    return "\n".join(lines)


def board_state() -> str:
    """What is on the board right now — Apex's eyes, so it can look before it
    talks. The user moves things by hand, so memory is never the answer."""
    if not is_configured():
        return "[Barehands] disabled (BAREHANDS_ENABLED=false)."
    body = _get("/state")
    if body is None:
        return "[Barehands] the board is dark — the barehands server isn't running."
    try:
        data = json.loads(body)
    except Exception:
        return "[Barehands] the board returned something unreadable."
    if not isinstance(data, dict) or ("cursors" not in data and "items" not in data):
        return "[Barehands] barehands is up, but no stage has connected yet."

    items = data.get("items") or []
    hands = len(data.get("cursors") or [])
    if not items:
        return f"The board is empty. ({hands} hand(s) in view.)"

    lines = [f"On the board — {len(items)} item(s), {hands} hand(s) in view:"]
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = it.get("type", "?")
        title = it.get("title") or ""
        src = str(it.get("src") or "").rsplit("/", 1)[-1]
        label = title or src or kind
        flags = []
        if it.get("g"):
            flags.append("in your hand")
        try:
            if float(it.get("scale") or 1) >= 1.6:
                flags.append("blown up")
        except (TypeError, ValueError):
            pass
        lines.append(f"  - {kind}: {label}" + (f"  [{', '.join(flags)}]" if flags else ""))
    return "\n".join(lines)
