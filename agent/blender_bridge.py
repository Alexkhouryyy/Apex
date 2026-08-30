"""Voice-driven 3D creation — "Apex, create a red cube, 50 millimetres wide."

Blender is a real, separately-installed application; `bpy` (its Python API) is
only safe to call from Blender's own main thread. Apex cannot bundle or launch
it. So the boundary is a small ADDON that runs INSIDE Blender
(`blender/apex_blender_addon.py` — installed once via Blender's Preferences)
and this module, a thin client that talks to it over one loopback socket. There
is no second Apex process here, no separate repo, no bridge service of Apex's
own — the same aggregation posture the MCP client already has: Apex drives a
separate program over localhost, and stays what it is.

## The one rule this file exists to enforce

The report that specified this feature calls unrestricted Python-execution-
inside-Blender an explicitly EXCLUDED capability — "Structured actions over
improvisation. Voice is converted into typed commands with units, object IDs,
and explicit operations," not code. The addon on the other end already refuses
anything outside its fixed command set. This module enforces the SAME
allowlist again, independently, before a single byte reaches the socket:

  * `shape` must be one of `SHAPES` — nothing free-form reaches Blender's API.
  * every dimension is a finite number inside `[BLENDER_MIN_DIM_MM,
    BLENDER_MAX_DIM_MM]` — not because Blender would crash on `1e30`, but
    because that number is certainly a typo or a hallucinated unit, not a real
    request, and creating it anyway would look like success.
  * `name` is slugified before it becomes a Blender object name AND a filename.
    Unslugified, a name is a path: this codebase already found exactly this
    shape of bug twice this session — `.env` values that smuggled a second
    assignment, prop paths that walked out of their jail. A created object's
    name is user- or model-supplied text reaching a filesystem path the same
    way, and gets the same discipline.

Two independent gates matter more than one careful gate: if either side is
ever wired up wrong, the other still holds.
"""
from __future__ import annotations

import json
import re
import socket
import time
import uuid
from typing import Optional

import config

# Deliberately short. Each is a decision that Blender's create_primitive
# operator on the other end actually knows how to build — adding a shape here
# without adding it to the addon fails at the socket, not silently.
SHAPES = frozenset({"cube", "sphere", "cylinder", "cone", "plane", "torus"})

# Which dimensions each shape needs, and in what order they're reported back on
# a validation failure — so "cylinder needs height" is an actual answer, not a
# guess. Every shape needs at least one dimension; nothing here is optional.
SHAPE_DIMS: dict[str, tuple[str, ...]] = {
    "cube": ("width", "depth", "height"),
    "plane": ("width", "depth"),
    "sphere": ("diameter",),
    "cylinder": ("diameter", "height"),
    "cone": ("diameter", "height"),
    "torus": ("diameter", "tube_diameter"),
}

# A small named palette so "make it metallic blue" doesn't require the model to
# invent RGB values. Anything not on this list can still be given as
# "#rrggbb" or an [r, g, b] triplet in 0..1 — this is a convenience, not the
# only way in.
COLOR_NAMES: dict[str, tuple[float, float, float, float]] = {
    "red": (0.8, 0.05, 0.05, 1.0),
    "green": (0.05, 0.6, 0.15, 1.0),
    "blue": (0.05, 0.15, 0.75, 1.0),
    "metallic_blue": (0.05, 0.2, 0.55, 1.0),
    "white": (0.9, 0.9, 0.9, 1.0),
    "black": (0.02, 0.02, 0.02, 1.0),
    "matte_black": (0.03, 0.03, 0.03, 1.0),
    "grey": (0.5, 0.5, 0.5, 1.0),
    "gray": (0.5, 0.5, 0.5, 1.0),
    "yellow": (0.85, 0.7, 0.05, 1.0),
    "orange": (0.85, 0.4, 0.05, 1.0),
    "purple": (0.4, 0.1, 0.6, 1.0),
}

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


class BlenderError(Exception):
    """A refusal or a failed round-trip — always carries a human message."""


def slugify(name: str, fallback_prefix: str = "object") -> str:
    """Turn arbitrary text into a safe Blender object name AND filename stem.

    Lowercase, ascii, hyphens only, capped length. This becomes a path
    component (`<slug>.glb` under the export directory) and a Blender object
    name, so `../../evil` or an empty string must never survive this function —
    the same containment discipline as `agent/props.py`, applied at the one
    other place in this codebase where user-influenced text turns into a path.
    """
    s = (name or "").strip().lower()
    s = s.replace(" ", "-")
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = f"{fallback_prefix}-{uuid.uuid4().hex[:6]}"
    return s[:60]


def resolve_color(color) -> Optional[tuple]:
    """Accepts a name from COLOR_NAMES, a "#rrggbb" hex string, or an [r,g,b]
    / [r,g,b,a] sequence in 0..1. Returns None — never raises — on anything
    else, so a caller can report "unknown colour" rather than crash."""
    if color is None:
        return None
    if isinstance(color, str):
        key = color.strip().lower().replace(" ", "_")
        if key in COLOR_NAMES:
            return COLOR_NAMES[key]
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color.strip())
        if m:
            hexs = m.group(1)
            r, g, b = (int(hexs[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            return (r, g, b, 1.0)
        return None
    if isinstance(color, (list, tuple)) and len(color) in (3, 4):
        try:
            vals = [float(v) for v in color]
        except (TypeError, ValueError):
            return None
        if not all(0.0 <= v <= 1.0 for v in vals):
            return None
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)
    return None


def validate_dims(shape: str, dims_mm: dict) -> tuple[Optional[dict], str]:
    """(dims, "") on success; (None, reason) on refusal.

    Every dimension required for `shape` must be present, numeric, finite, and
    inside [BLENDER_MIN_DIM_MM, BLENDER_MAX_DIM_MM] — a cube spanning 1e9 mm or
    -5 mm is certainly a typo or a hallucinated unit, and creating it anyway
    would report success for a request that was never real.
    """
    if shape not in SHAPE_DIMS:
        return None, f"unknown shape '{shape}'. Choose from: {', '.join(sorted(SHAPES))}"
    needed = SHAPE_DIMS[shape]
    if not isinstance(dims_mm, dict):
        return None, f"{shape} needs dims_mm with: {', '.join(needed)}"
    lo = float(getattr(config, "BLENDER_MIN_DIM_MM", 1))
    hi = float(getattr(config, "BLENDER_MAX_DIM_MM", 4000))
    out = {}
    for key in needed:
        if key not in dims_mm:
            return None, f"{shape} is missing '{key}' (needs: {', '.join(needed)})"
        try:
            v = float(dims_mm[key])
        except (TypeError, ValueError):
            return None, f"'{key}' must be a number, got {dims_mm[key]!r}"
        if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
            return None, f"'{key}' must be a real number, got {dims_mm[key]!r}"
        if not (lo <= v <= hi):
            return None, (f"'{key}'={v}mm is outside the sane range "
                          f"[{lo}, {hi}]mm — check the units")
        out[key] = v
    return out, ""


def _send(cmd: dict, timeout: Optional[float] = None) -> dict:
    """One newline-delimited JSON request/response over the addon's socket.

    A bare function, not a class, so tests can monkeypatch `blender_bridge._send`
    to a fake and exercise every validation path above with no Blender, no
    socket, and no network — the same seam `agent/props.py`'s serving tests use.
    """
    timeout = timeout if timeout is not None else config.BLENDER_TIMEOUT_SECONDS
    try:
        with socket.create_connection(
                (config.BLENDER_HOST, config.BLENDER_PORT), timeout=timeout) as s:
            s.sendall((json.dumps(cmd) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                raise BlenderError(
                    "the Blender add-on closed the connection with no reply — "
                    "check the Blender console for an error.")
            return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except ConnectionRefusedError:
        raise BlenderError(
            f"nothing is listening on {config.BLENDER_HOST}:{config.BLENDER_PORT} "
            f"— Blender isn't running, or the Apex add-on server isn't started "
            f"(Blender's N-panel > Apex tab > Start Server).")
    except socket.timeout:
        raise BlenderError(
            f"Blender didn't answer within {timeout}s — it may be busy or stuck.")
    except OSError as e:
        raise BlenderError(f"couldn't reach Blender: {e}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BlenderError("Blender's add-on sent back something that wasn't JSON.")


def available() -> bool:
    if not getattr(config, "BLENDER_ENABLED", False):
        return False
    try:
        resp = _send({"cmd": "ping"}, timeout=2.0)
        return bool(resp.get("ok"))
    except BlenderError:
        return False


def create_object(shape: str, dims_mm: dict, color=None,
                   name: str = "") -> dict:
    """Validate, then create + colour + export in one round trip per step.

    Returns {"name": <blender object name>, "slug": <filename stem>,
    "export_dir": <where the addon wrote the .glb>, "filename": <str>}.
    Raises BlenderError on any refusal — validation failures never touch the
    socket at all, which is the point of separating them from `_send`.
    """
    if not getattr(config, "BLENDER_ENABLED", False):
        raise BlenderError(
            "BLENDER_ENABLED is off. Set it in .env and install the add-on "
            "(blender/apex_blender_addon.py) in Blender first.")
    shape = (shape or "").strip().lower()
    dims, reason = validate_dims(shape, dims_mm or {})
    if dims is None:
        raise BlenderError(reason)
    rgba = None
    if color is not None:
        rgba = resolve_color(color)
        if rgba is None:
            raise BlenderError(
                f"unknown colour {color!r}. Try a name like "
                f"{', '.join(sorted(COLOR_NAMES)[:5])}… or '#rrggbb'.")
    slug = slugify(name or shape)

    resp = _send({"cmd": "create_primitive", "shape": shape, "dims_mm": dims,
                 "name": slug, "color": list(rgba) if rgba else None})
    if not resp.get("ok"):
        raise BlenderError(resp.get("error") or "Blender refused create_primitive.")
    blender_name = resp.get("name") or slug

    filename = f"{slug}-{uuid.uuid4().hex[:8]}.glb"
    resp = _send({"cmd": "export_glb", "name": blender_name, "filename": filename})
    if not resp.get("ok"):
        raise BlenderError(resp.get("error") or "Blender refused export_glb.")

    return {"name": blender_name, "slug": slug, "filename": filename,
            "export_dir": resp.get("export_dir", "")}


def recolor_object(blender_name: str, color) -> dict:
    """Set colour and re-export as a NEW file — never overwrite the last export.

    "A failed generation never replaces the last valid asset" was one of the
    versioning rules in the spec this feature came from; giving every recolor a
    fresh filename is what makes that true here rather than just documented.
    """
    if not getattr(config, "BLENDER_ENABLED", False):
        raise BlenderError("BLENDER_ENABLED is off.")
    rgba = resolve_color(color)
    if rgba is None:
        raise BlenderError(f"unknown colour {color!r}.")
    resp = _send({"cmd": "set_color", "name": blender_name, "color": list(rgba)})
    if not resp.get("ok"):
        raise BlenderError(resp.get("error") or "Blender refused set_color.")

    slug = slugify(blender_name)
    filename = f"{slug}-{uuid.uuid4().hex[:8]}.glb"
    resp = _send({"cmd": "export_glb", "name": blender_name, "filename": filename})
    if not resp.get("ok"):
        raise BlenderError(resp.get("error") or "Blender refused export_glb.")
    return {"name": blender_name, "slug": slug, "filename": filename,
            "export_dir": resp.get("export_dir", "")}
