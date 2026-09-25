"""Grab one part of a model: which part a pinch is on, and what moving it means.

The board's hand logic runs here in Python and the page only draws, so to
pick "the nose" under your fingers Python has to know where the page draws
every part. It makes the SAME transform the page does — board.html's
fitToUnitBox and placeModel — from the build's recipe (agent/build3d.py):

    screen = T + S · Rx(tilt) · Ry(rot) · (p − c)

    p      a point of the model, metres
    c, L   the centre and largest side of the whole model's bounding box
           (fitToUnitBox centres it and scales its largest side to FIT)
    Ry     the card's rotation (the two-hand twist), about the model's up
    Rx     the fixed tilt that shows the top face (MODEL_TILT)
    S      diag(k, k·aspect, k), k = FIT / L · card scale — the aspect term
           is placeModel's correction for a camera that spans a wide window
           as 1 across and 1 down
    T      (card x, −card y, 0); board y runs down, the camera's y up

A board point is (screen x, −screen y); larger z is nearer the viewer.
scripts/check_build3d_glb.mjs runs the page's own functions and this module
on the same model and fails if they disagree about where a part is drawn.

Only builds have parts to grab: a recipe says what the parts are. A model
dropped into the props folder is still grabbed whole.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Optional

import numpy as np

FIT = 0.18            # board.html fitToUnitBox: the largest side, in board widths
TILT = 0.35           # board.html MODEL_TILT
PICK_SLOP = 0.02      # a pinch this close to a part's drawn outline still gets it

_SRC = re.compile(r"^created/([a-z0-9-]+)/v(\d+)\.glb$")


def recipe_for_src(src: str) -> Optional[tuple[str, list[dict]]]:
    """(title, parts) of the build version a card shows, or None when the
    card is not a build (a dropped-in prop, a Blender export, a text card)."""
    m = _SRC.match(src or "")
    if not m:
        return None
    from agent import assets
    data = assets.load(m.group(1))
    if not data:
        return None
    n = int(m.group(2))
    for v in data.get("versions", []):
        if v.get("version") == n:
            cmd = v.get("command") or {}
            if cmd.get("tool") == "board_build" and cmd.get("parts"):
                return data.get("title") or m.group(1), cmd["parts"]
    return None


def _key(parts: list[dict]) -> tuple:
    return tuple((p["shape"], tuple(p["size"]), tuple(p["at"]), tuple(p["rotate"])) for p in parts)


@lru_cache(maxsize=32)
def _meshes(key: tuple):
    from agent import build3d
    parts = [{"shape": s, "size": list(z), "at": list(a), "rotate": list(r)} for s, z, a, r in key]
    return [build3d.part_mesh(p) for p in parts]


def fit(parts: list[dict]) -> tuple[np.ndarray, float]:
    """The whole model's bounding-box centre and largest side, in metres."""
    pts = np.concatenate([m[0] for m in _meshes(_key(parts))])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return (lo + hi) / 2.0, float(max(hi - lo)) or 1.0


def _rot(rot: float) -> np.ndarray:
    ct, st, cr, sr = math.cos(TILT), math.sin(TILT), math.cos(rot), math.sin(rot)
    rx = np.array([[1, 0, 0], [0, ct, -st], [0, st, ct]])
    ry = np.array([[cr, 0, sr], [0, 1, 0], [-sr, 0, cr]])
    return rx @ ry


def _scale(card: dict, largest: float, aspect: float) -> np.ndarray:
    k = FIT / largest * float(card.get("scale") or 1.0)
    return np.array([k, k * aspect, k])


def project(card: dict, parts: list[dict], points: np.ndarray, aspect: float) -> np.ndarray:
    """Model points (metres) -> (board x, board y, depth) as the page draws them."""
    c, largest = fit(parts)
    world = (points - c) @ _rot(float(card.get("rot") or 0.0)).T * _scale(card, largest, aspect)
    world += np.array([card["x"], -card["y"], 0.0])
    return np.column_stack([world[:, 0], -world[:, 1], world[:, 2]])


def pick(card: dict, parts: list[dict], hx: float, hy: float, aspect: float) -> Optional[int]:
    """The part drawn under (hx, hy) nearest the viewer, or None.

    Tested against every triangle as drawn, not a bounding box: the nose of a
    rocket sits inside the box of the body behind it, and a box test would
    hand you the body. With nothing exactly under the fingers, the nearest
    part within PICK_SLOP — a pinch is not a mouse click.
    """
    best, best_z = None, -math.inf
    near, near_d = None, PICK_SLOP
    for i, (pos, _nrm, idx) in enumerate(_meshes(_key(parts))):
        s = project(card, parts, pos, aspect)
        t = idx.reshape(-1, 3)
        a, b, cc = s[t[:, 0]], s[t[:, 1]], s[t[:, 2]]
        v0, v1 = b[:, :2] - a[:, :2], cc[:, :2] - a[:, :2]
        v2 = np.array([hx, hy]) - a[:, :2]
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        ok = np.abs(den) > 1e-12
        u = np.where(ok, (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / np.where(ok, den, 1), -1)
        v = np.where(ok, (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / np.where(ok, den, 1), -1)
        inside = ok & (u >= 0) & (v >= 0) & (u + v <= 1)
        if inside.any():
            z = (a[:, 2] * (1 - u - v) + b[:, 2] * u + cc[:, 2] * v)[inside].max()
            if z > best_z:
                best, best_z = i, z
        d = float(np.min(np.hypot(s[:, 0] - hx, s[:, 1] - hy)))
        if d < near_d:
            near, near_d = i, d
    return best if best is not None else near


def model_delta(card: dict, parts: list[dict], dsx: float, dsy: float, aspect: float) -> np.ndarray:
    """A hand movement on the board -> the same movement in the model's own
    space, in metres, parallel to the screen (no depth change)."""
    _c, largest = fit(parts)
    world = np.array([dsx, -dsy, 0.0]) / _scale(card, largest, aspect)
    return _rot(float(card.get("rot") or 0.0)).T @ world


def edited(parts: list[dict], index: int, offset_m, factor: float) -> list[dict]:
    """The recipe with one part moved (metres) and resized about its centre."""
    out = [dict(p) for p in parts]
    p = out[index]
    p["at"] = [round(a + o * 100.0, 3) for a, o in zip(p["at"], offset_m)]
    p["size"] = [round(s * factor, 3) for s in p["size"]]
    return out


def compensate(card: dict, old: list[dict], new: list[dict], aspect: float) -> dict:
    """Card x, y and scale that keep the parts you did NOT touch exactly where
    they were on screen, although the new version has a different bounding
    box (the page refits every model to FIT) — without this the whole model
    would jump or shrink the moment you let go of one part."""
    c0, l0 = fit(old)
    c1, l1 = fit(new)
    scale = float(card.get("scale") or 1.0) * l1 / l0
    shift = (c1 - c0) @ _rot(float(card.get("rot") or 0.0)).T * _scale(card, l0, aspect)
    return {"x": card["x"] + float(shift[0]), "y": card["y"] - float(shift[1]), "scale": scale}
