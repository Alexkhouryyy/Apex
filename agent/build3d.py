"""Build 3D with words — "Apex, build me a rocket" — no Blender needed.

`board_create` makes one measured primitive, and only with Blender open and its
add-on running. Most of what people want to see on the board is not one
primitive and not a manufacturing drawing: a rocket, a chair, a molecule, a
desk layout — a recognisable object to pick up, turn and resize with two
hands. The model is good at describing such a thing as parts ("a white
cylinder 60 cm tall, a red cone on top, three fins…"); this module turns that
description into a real `.glb` that the board, and any other 3D program, can
open.

## The recipe

A build is a list of parts. Every part is a primitive fitted into a box:

    {"shape": "cylinder", "size": [20, 60, 20], "at": [0, 30, 0],
     "rotate": [0, 0, 0], "color": "white", "name": "body"}

* `shape`  — box | sphere | cylinder | cone | torus. Nothing else: the
  recipe is data, never code, the same rule `blender_bridge` enforces.
* `size`   — the part's bounding box in centimetres, [width, height, depth]
  before rotation. A sphere with unequal sizes is an ellipsoid; a cylinder
  with unequal width and depth is elliptical. A torus's height is its tube
  thickness and width/depth its outer diameter.
* `at`     — the centre, in centimetres. Y is up.
* `rotate` — degrees about X, then Y, then Z (optional).
* `color`  — a name from `blender_bridge.COLOR_NAMES`, or "#rrggbb" (optional).
* `metal`  — true for a metallic finish (optional).

The file is glTF 2.0 binary in METRES, glTF's own unit, so a 60 cm part is
0.6 in the file and opens at its true size elsewhere. The recipe is kept
verbatim in the asset's version record (`agent/assets.py`), so a build can be
shown, revised part by part, and every earlier version stays.

No dependency beyond numpy: the meshes and the GLB container are written
here. `tests/test_build3d.py` checks the container byte by byte, and
`scripts/check_build3d_glb.mjs` loads the result with the three.js loader the
board itself uses.
"""
from __future__ import annotations

import json
import math
import struct
from typing import Optional

import numpy as np

SHAPES = ("box", "sphere", "cylinder", "cone", "torus")
MAX_PARTS = 80
MIN_CM, MAX_CM = 0.05, 1000.0       # a part from half a millimetre to 10 m
MAX_OFFSET_CM = 2000.0
SEGMENTS = 32                       # around cylinders, cones, spheres, tori
RINGS = 16                          # pole to pole on a sphere
# The unit torus: outer radius 0.5 so width and depth are its diameter, and a
# tube radius whose thickness (2 x TUBE) is rescaled to the part's height.
TORUS_R, TORUS_TUBE = 0.35, 0.15


class BuildError(ValueError):
    """A recipe that cannot be built — always a sentence the model can act on."""


# --- the recipe ------------------------------------------------------------

def _vec(value, n: int, what: str, lo: float, hi: float, default=None,
         scalar: bool = False) -> list[float]:
    if value is None and default is not None:
        return list(default)
    if scalar and isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value] * n                     # one number: the same in every direction
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise BuildError(f"{what} must be a list of {n} numbers")
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise BuildError(f"{what} must be a list of {n} finite numbers")
        if not lo <= v <= hi:
            raise BuildError(f"{what} {list(value)} is outside {lo}..{hi}")
        out.append(float(v))
    return out


def validate(parts) -> list[dict]:
    """The recipe, checked and normalised, or BuildError naming the first
    problem and the part it is in."""
    from agent.blender_bridge import resolve_color
    if not isinstance(parts, list) or not parts:
        raise BuildError("parts must be a non-empty list")
    if len(parts) > MAX_PARTS:
        raise BuildError(f"{len(parts)} parts — at most {MAX_PARTS}; merge small details")
    clean = []
    for i, p in enumerate(parts, 1):
        where = f"part {i}"
        if not isinstance(p, dict):
            raise BuildError(f"{where} must be an object")
        shape = str(p.get("shape", "")).strip().lower()
        if shape == "cube":
            shape = "box"
        if shape not in SHAPES:
            raise BuildError(f"{where}: shape '{p.get('shape')}' — use one of {', '.join(SHAPES)}")
        where = f"part {i} ({p.get('name') or shape})"
        size = _vec(p.get("size"), 3, f"{where} size (cm)", MIN_CM, MAX_CM, scalar=True)
        at = _vec(p.get("at"), 3, f"{where} at (cm)", -MAX_OFFSET_CM, MAX_OFFSET_CM, default=(0, 0, 0))
        rot = _vec(p.get("rotate"), 3, f"{where} rotate (degrees)", -3600, 3600, default=(0, 0, 0))
        color = p.get("color") or "grey"
        rgba = resolve_color(color)
        if rgba is None:
            raise BuildError(f"{where}: colour '{color}' — use a plain name like red or '#rrggbb'")
        clean.append({"shape": shape, "size": size, "at": at, "rotate": rot,
                      "color": [round(c, 4) for c in rgba], "metal": bool(p.get("metal")),
                      "name": str(p.get("name") or shape)[:60]})
    return clean


# --- unit meshes: each fits the box [-0.5, 0.5] in every axis --------------

def _box():
    pos, nrm, idx = [], [], []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            n = np.zeros(3); n[axis] = sign
            u = np.zeros(3); u[(axis + 1) % 3] = 1.0
            v = np.cross(n, u)
            base = len(pos)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                pos.append(0.5 * n + 0.5 * a * u + 0.5 * b * v); nrm.append(n)
            idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return np.array(pos), np.array(nrm), np.array(idx)


def _sphere():
    pos, nrm, idx = [], [], []
    for r in range(RINGS + 1):
        th = math.pi * r / RINGS
        for s in range(SEGMENTS + 1):
            ph = 2 * math.pi * s / SEGMENTS
            n = np.array([math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph)])
            pos.append(0.5 * n); nrm.append(n)
    w = SEGMENTS + 1
    for r in range(RINGS):
        for s in range(SEGMENTS):
            a, b = r * w + s, (r + 1) * w + s
            idx += [a, a + 1, b, a + 1, b + 1, b]
    return np.array(pos), np.array(nrm), np.array(idx)


def _cap(pos, nrm, idx, y, radius_at, up):
    """A flat disc at height y, facing up (+1) or down (-1)."""
    n = np.array([0.0, up, 0.0])
    centre = len(pos)
    pos.append(np.array([0.0, y, 0.0])); nrm.append(n)
    for s in range(SEGMENTS + 1):
        ph = 2 * math.pi * s / SEGMENTS
        pos.append(np.array([radius_at * math.cos(ph), y, radius_at * math.sin(ph)])); nrm.append(n)
    for s in range(SEGMENTS):
        a, b = centre + 1 + s, centre + 2 + s
        idx += [centre, b, a] if up > 0 else [centre, a, b]


def _cylinder(top_radius: float = 0.5):
    """A cylinder; with top_radius 0, a cone. Side normals lean outward by the
    slope, so a cone is shaded as a cone and not as a cylinder."""
    pos, nrm, idx = [], [], []
    slope = 0.5 - top_radius                         # radius lost over height 1
    for s in range(SEGMENTS + 1):
        ph = 2 * math.pi * s / SEGMENTS
        c, sn = math.cos(ph), math.sin(ph)
        n = np.array([c, slope, sn]); n /= np.linalg.norm(n)
        pos.append(np.array([0.5 * c, -0.5, 0.5 * sn])); nrm.append(n)
        pos.append(np.array([top_radius * c, 0.5, top_radius * sn])); nrm.append(n)
    for s in range(SEGMENTS):
        a = 2 * s
        idx += [a, a + 1, a + 2, a + 1, a + 3, a + 2]
    _cap(pos, nrm, idx, -0.5, 0.5, -1.0)
    if top_radius > 0:
        _cap(pos, nrm, idx, 0.5, top_radius, 1.0)
    return np.array(pos), np.array(nrm), np.array(idx)


def _torus():
    pos, nrm, idx = [], [], []
    for i in range(SEGMENTS + 1):
        u = 2 * math.pi * i / SEGMENTS
        for j in range(SEGMENTS // 2 + 1):
            v = 2 * math.pi * j / (SEGMENTS // 2)
            centre = np.array([TORUS_R * math.cos(u), 0.0, TORUS_R * math.sin(u)])
            n = np.array([math.cos(v) * math.cos(u), math.sin(v), math.cos(v) * math.sin(u)])
            pos.append(centre + TORUS_TUBE * n); nrm.append(n)
    w = SEGMENTS // 2 + 1
    for i in range(SEGMENTS):
        for j in range(SEGMENTS // 2):
            a, b = i * w + j, (i + 1) * w + j
            idx += [a, a + 1, b, a + 1, b + 1, b]
    return np.array(pos), np.array(nrm), np.array(idx)


def unit_mesh(shape: str):
    if shape == "box":
        return _box()
    if shape == "sphere":
        return _sphere()
    if shape == "cylinder":
        return _cylinder(0.5)
    if shape == "cone":
        return _cylinder(0.0)
    if shape == "torus":
        p, n, i = _torus()
        # Stretch the tube's thickness (0.3) to the unit height, so `size`
        # means the same thing for a torus as for everything else.
        p = p * np.array([1.0, 1.0 / (2 * TORUS_TUBE), 1.0])
        n = n * np.array([1.0, 2 * TORUS_TUBE, 1.0])
        return p, n / np.linalg.norm(n, axis=1, keepdims=True), i
    raise BuildError(f"unknown shape {shape}")


def _rotation(deg) -> np.ndarray:
    rx, ry, rz = (math.radians(d) for d in deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Z @ Y @ X                     # X first, then Y, then Z


def part_mesh(part: dict):
    """One part's triangles, placed, in metres: (positions, normals, indices)."""
    p, n, i = unit_mesh(part["shape"])
    scale = np.array(part["size"]) / 100.0
    R = _rotation(part["rotate"])
    positions = (p * scale) @ R.T + np.array(part["at"]) / 100.0
    # Normals take the inverse scale (the inverse-transpose of R·S), or a
    # squashed sphere would be lit as if it were round.
    normals = (n / scale) @ R.T
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    return positions.astype(np.float32), normals.astype(np.float32), i.astype(np.uint32)


# --- the GLB container -----------------------------------------------------

def to_glb(parts: list[dict], title: str = "build") -> bytes:
    """A glTF 2.0 binary: one node and one mesh per part (named, so a part can
    be found again), under one root node named after the build."""
    blob = bytearray()
    views, accessors, meshes, nodes, materials = [], [], [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        blob.extend(data)
        return len(views) - 1

    for k, part in enumerate(parts):
        pos, nrm, idx = part_mesh(part)
        pv = add_view(pos.tobytes(), 34962)
        nv = add_view(nrm.tobytes(), 34962)
        iv = add_view(idx.tobytes(), 34963)
        accessors += [
            {"bufferView": pv, "componentType": 5126, "count": len(pos), "type": "VEC3",
             "min": [float(v) for v in pos.min(axis=0)], "max": [float(v) for v in pos.max(axis=0)]},
            {"bufferView": nv, "componentType": 5126, "count": len(nrm), "type": "VEC3"},
            {"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        ]
        a = 3 * k
        materials.append({"name": f"{part['name']}-material",
                          "pbrMetallicRoughness": {"baseColorFactor": part["color"],
                                                   "metallicFactor": 0.8 if part["metal"] else 0.05,
                                                   "roughnessFactor": 0.35 if part["metal"] else 0.6}})
        meshes.append({"name": part["name"], "primitives": [
            {"attributes": {"POSITION": a, "NORMAL": a + 1}, "indices": a + 2, "material": k}]})
        nodes.append({"name": part["name"], "mesh": k})
    nodes.append({"name": title, "children": list(range(len(parts)))})
    while len(blob) % 4:
        blob.append(0)
    doc = {"asset": {"version": "2.0", "generator": "Apex build3d"},
           "scene": 0, "scenes": [{"name": title, "nodes": [len(nodes) - 1]}],
           "nodes": nodes, "meshes": meshes, "materials": materials,
           "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


def extent_cm(parts: list[dict]) -> list[float]:
    """The whole build's width, height and depth in centimetres."""
    pts = np.concatenate([part_mesh(p)[0] for p in parts])
    return [round(float(v) * 100, 1) for v in pts.max(axis=0) - pts.min(axis=0)]


# --- saving it as a versioned asset ----------------------------------------

def build(title: str, parts, props_root=None) -> dict:
    """Validate, write the next version of the asset named `title`, and return
    {slug, src, version, parent, parts, extent_cm, revised}. A recipe that
    fails validation writes nothing and records no version."""
    from agent import assets
    from agent.blender_bridge import slugify
    title = " ".join(str(title or "").split())[:80] or "build"
    clean = validate(parts)
    data = to_glb(clean, title)
    existing = assets.find_by_title(title, props_root)
    slug = existing["id"] if existing else slugify(title, "build")
    parent = existing.get("current_version") if existing else None
    command = {"tool": "board_build", "parts": clean}
    if not existing:
        # A different asset may already own this slug under another title;
        # never append this build to its history.
        if assets.load(slug, props_root) is not None:
            import uuid
            slug = f"{slug[:52]}-{uuid.uuid4().hex[:6]}"
        assets.create(slug, title, command=command, units="m", props_root=props_root)
    filename = assets.next_filename(slug, props_root)
    root = assets.asset_root(slug, props_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_bytes(data)
    record = assets.add_version(slug, filename, command=command, parent=parent, props_root=props_root)
    return {"slug": slug, "src": f"{assets.CREATED_DIR}/{slug}/{filename}",
            "version": record["version"], "parent": parent, "parts": len(clean),
            "extent_cm": extent_cm(clean), "revised": existing is not None}


def recipe(title: str, props_root=None) -> Optional[list[dict]]:
    """The parts of the current version of a build, or None."""
    from agent import assets
    data = assets.find_by_title(title, props_root)
    if not data or not data.get("current_version"):
        return None
    for v in data.get("versions", []):
        if v.get("version") == data["current_version"]:
            cmd = v.get("command") or {}
            return cmd.get("parts") if cmd.get("tool") == "board_build" else None
    return None
