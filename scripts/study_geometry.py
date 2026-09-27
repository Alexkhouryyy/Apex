"""Small mesh toolkit for the study library's generated models (numpy only).

Every shape is a grid of points (revolved profiles, swept tubes, lofted
blades, boxes, extrusions) turned into indexed triangles with smooth normals,
then written as a glTF 2.0 binary with one named node per study component.
Used by scripts/build_study_models.py; nothing at runtime imports it.
"""
from __future__ import annotations

import json
import math
import struct

import numpy as np

TAU = 2 * math.pi


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n < 1e-12, 1, n)


def _normals(pos, tris):
    face = np.cross(pos[tris[:, 1]] - pos[tris[:, 0]], pos[tris[:, 2]] - pos[tris[:, 0]])
    acc = np.zeros_like(pos)
    for k in range(3):
        np.add.at(acc, tris[:, k], face)
    return _unit(acc)


class Mesh:
    def __init__(self, pos, tris, nrm=None):
        self.pos = np.asarray(pos, float).reshape(-1, 3)
        self.tris = np.asarray(tris, np.int64).reshape(-1, 3)
        self.nrm = _normals(self.pos, self.tris) if nrm is None else np.asarray(nrm, float)

    def moved(self, R=None, t=(0, 0, 0)):
        R = np.eye(3) if R is None else np.asarray(R, float)
        tris = self.tris[:, ::-1] if np.linalg.det(R) < 0 else self.tris
        return Mesh(self.pos @ R.T + np.asarray(t, float), tris, _unit(self.nrm @ R.T))

    @staticmethod
    def merge(meshes):
        pos, nrm, tris, base = [], [], [], 0
        for m in meshes:
            pos.append(m.pos); nrm.append(m.nrm); tris.append(m.tris + base); base += len(m.pos)
        return Mesh(np.concatenate(pos), np.concatenate(tris), np.concatenate(nrm))


def grid(P, wrap_u=False, wrap_v=False, flip=False):
    """P[nu, nv, 3] -> quads (two triangles each) with smooth normals."""
    P = np.asarray(P, float)
    nu, nv = P.shape[:2]
    i, j = np.meshgrid(np.arange(nu if wrap_u else nu - 1), np.arange(nv if wrap_v else nv - 1), indexing='ij')
    a = i * nv + j
    b = ((i + 1) % nu) * nv + j
    c = ((i + 1) % nu) * nv + (j + 1) % nv
    d = i * nv + (j + 1) % nv
    tris = np.stack([a, b, c, a, c, d], -1).reshape(-1, 3)
    return Mesh(P.reshape(-1, 3), tris[:, ::-1] if flip else tris)


def frame(axis, ref=None):
    """An orthonormal frame (e0 along axis, e1, e2)."""
    e0 = _unit(axis)
    if ref is None:
        ref = (0, 1, 0) if abs(e0[1]) < 0.9 else (0, 0, 1)
    e1 = _unit(np.asarray(ref, float) - np.dot(ref, e0) * e0)
    return e0, e1, np.cross(e0, e1)


def _angles(seg, start, arc):
    full = abs(arc - TAU) < 1e-9
    return (start + arc * np.arange(seg) / seg if full else np.linspace(start, start + arc, seg + 1)), full


def revolve(profile, origin=(0, 0, 0), axis=(1, 0, 0), ref=None, seg=48, start=0.0, arc=TAU,
            squash=(1.0, 1.0), rmul=None, flip=False):
    """Revolve [(along, radius), ...] about an axis. `rmul(along, angle)` shapes organic forms."""
    e0, e1, e2 = frame(axis, ref)
    prof = np.asarray(profile, float)
    ang, full = _angles(seg, start, arc)
    x = prof[:, 0][:, None]
    r = prof[:, 1][:, None] * (rmul(x, ang[None, :]) if rmul else 1.0)
    P = (np.asarray(origin, float) + x[..., None] * e0
         + (r * np.cos(ang) * squash[0])[..., None] * e1 + (r * np.sin(ang) * squash[1])[..., None] * e2)
    return grid(P, wrap_v=full, flip=not flip)


def shell(outer, inner, seg=64, start=0.0, arc=TAU, **kw):
    """A walled solid of revolution: outer and inner profiles (same stations),
    joined at both ends, and closed at the cut if it is a partial (cutaway) turn."""
    outer, inner = np.asarray(outer, float), np.asarray(inner, float)
    parts = [revolve(outer, seg=seg, start=start, arc=arc, **kw),
             revolve(inner, seg=seg, start=start, arc=arc, flip=True, **kw),
             revolve([inner[0], outer[0]], seg=seg, start=start, arc=arc, **kw),
             revolve([outer[-1], inner[-1]], seg=seg, start=start, arc=arc, **kw)]
    if abs(arc - TAU) > 1e-9:
        e0, e1, e2 = frame(kw.get('axis', (1, 0, 0)), kw.get('ref'))
        o = np.asarray(kw.get('origin', (0, 0, 0)), float)
        for a, flip in ((start, False), (start + arc, True)):
            d = math.cos(a) * e1 + math.sin(a) * e2
            P = np.stack([o + outer[:, :1] * e0 + outer[:, 1:] * d, o + inner[:, :1] * e0 + inner[:, 1:] * d], 1)
            parts.append(grid(P, flip=flip))
    return Mesh.merge(parts)


def cylinder(p0, p1, r, seg=32, r1=None):
    L = float(np.linalg.norm(np.subtract(p1, p0)))
    r1 = r if r1 is None else r1
    return Mesh.merge([revolve([(0, 0), (0, r)], p0, np.subtract(p1, p0), seg=seg),
                       revolve([(0, r), (L, r1)], p0, np.subtract(p1, p0), seg=seg),
                       revolve([(L, r1), (L, 0)], p0, np.subtract(p1, p0), seg=seg)])


def ring(center, axis, R, r, seg=48, tube_seg=12):
    """A torus."""
    e0, e1, e2 = frame(axis)
    u = np.linspace(0, TAU, seg, endpoint=False)[:, None]
    v = np.linspace(0, TAU, tube_seg, endpoint=False)[None, :]
    radial = np.cos(u)[..., None] * e1 + np.sin(u)[..., None] * e2
    P = np.asarray(center, float) + (R + r * np.cos(v))[..., None] * radial + (r * np.sin(v))[..., None] * e0
    return grid(P, True, True, flip=True)


def catmull(points, n=8, closed=False):
    """A smooth path through control points."""
    p = np.asarray(points, float)
    if closed:
        ext = np.concatenate([p[-1:], p, p[:2]])
    else:
        ext = np.concatenate([2 * p[:1] - p[1:2], p, 2 * p[-1:] - p[-2:-1]])
    out = []
    for k in range(len(p) if closed else len(p) - 1):
        p0, p1, p2, p3 = ext[k:k + 4]
        for t in np.linspace(0, 1, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    if not closed:
        out.append(p[-1])
    return np.array(out)


def _transport(path, closed):
    T = _unit(np.gradient(path, axis=0) if not closed else np.roll(path, -1, 0) - np.roll(path, 1, 0))
    N = np.zeros_like(path)
    ref = np.array([0, 1, 0]) if abs(T[0][1]) < 0.9 else np.array([1, 0, 0])
    N[0] = _unit(np.cross(T[0], ref))
    for k in range(1, len(path)):
        n = N[k - 1] - np.dot(N[k - 1], T[k]) * T[k]
        N[k] = _unit(n) if np.linalg.norm(n) > 1e-9 else N[k - 1]
    return T, N, np.cross(T, N)


def tube(path, radius, seg=14, closed=False, caps=True, profile=None):
    """Sweep a circle (or a closed 2D `profile` in the normal plane) along a path."""
    path = np.asarray(path, float)
    T, N, B = _transport(path, closed)
    r = np.broadcast_to(np.asarray(radius, float), (len(path),))[:, None]
    if profile is None:
        a = np.linspace(0, TAU, seg, endpoint=False)
        profile = np.stack([np.cos(a), np.sin(a)], 1)
    prof = np.asarray(profile, float)
    P = path[:, None, :] + r[..., None] * (prof[None, :, :1] * N[:, None, :] + prof[None, :, 1:] * B[:, None, :])
    m = grid(P, wrap_u=closed, wrap_v=True, flip=True)
    if caps and not closed:
        m = Mesh.merge([m, _cap(P[0], path[0], -T[0]), _cap(P[-1], path[-1], T[-1])])
    return m


def _cap(loop, center, n):
    pos = np.concatenate([[center], loop])
    k = len(loop)
    tris = np.array([[0, 1 + (i + 1) % k, 1 + i] for i in range(k)])
    m = Mesh(pos, tris, np.tile(n, (k + 1, 1)))
    face = np.cross(pos[tris[0, 1]] - pos[0], pos[tris[0, 2]] - pos[0])
    if np.dot(face, n) < 0:
        m.tris = m.tris[:, ::-1]
    return m


def box(center, size, R=None):
    """A box with flat faces."""
    h = np.asarray(size, float) / 2
    faces = []
    for ax in range(3):
        for s in (-1, 1):
            u, v = [(1, 2), (2, 0), (0, 1)][ax]
            quad = []
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = np.zeros(3); p[ax] = s * h[ax]; p[u] = a * h[u]; p[v] = b * h[v]
                quad.append(p)
            n = np.zeros(3); n[ax] = s
            tris = np.array([[0, 1, 2], [0, 2, 3]]) if s > 0 else np.array([[0, 2, 1], [0, 3, 2]])
            faces.append(Mesh(np.array(quad), tris, np.tile(n, (4, 1))))
    return Mesh.merge(faces).moved(R, center)


def between(p0, p1, width, depth, up=(0, 1, 0)):
    """A box beam from p0 to p1."""
    e0, e1, e2 = frame(np.subtract(p1, p0), up)
    R = np.stack([e0, e1, e2], 1)
    L = float(np.linalg.norm(np.subtract(p1, p0)))
    return box((np.add(p0, p1)) / 2, (L, width, depth), R)


def extrude(poly, origin, axis, depth, ref=None, smooth=True):
    """Extrude a closed 2D polygon (in the e1/e2 plane) along an axis."""
    e0, e1, e2 = frame(axis, ref)
    poly = np.asarray(poly, float)
    loop = np.asarray(origin, float) + poly[:, :1] * e1 + poly[:, 1:] * e2
    P = np.stack([loop, loop + depth * e0], 1)
    side = grid(P, wrap_u=True, flip=True)
    if not smooth:
        side = Mesh(side.pos, side.tris)
    c = loop.mean(0)
    return Mesh.merge([side, _cap(loop, c, -e0), _cap(loop + depth * e0, c + depth * e0, e0)])


def blade(axis_pos, radii, chords, twists, thick, angle, sweep=None, camber=0.06, n=10):
    """A twisted aerofoil blade about the X axis at azimuth `angle`.
    Each station: radius, chord (along the flow), twist (radians from the axis)."""
    t = np.linspace(0, TAU, n, endpoint=False)
    cs, ct = 0.5 * np.cos(t), np.sin(t)
    rows = []
    for k, r in enumerate(radii):
        c, tw, th = chords[k], twists[k], thick[k] if np.ndim(thick) else thick
        s = cs * c                                    # along the chord
        w = ct * th * (1 - (2 * cs) ** 2) ** 0.5 + camber * c * (1 - (2 * cs) ** 2)  # thickness + camber
        ax = axis_pos + (sweep[k] if sweep is not None else 0) + s * math.cos(tw) - w * math.sin(tw)
        tg = s * math.sin(tw) + w * math.cos(tw)
        rows.append(np.stack([ax, r * np.cos(angle) - tg * np.sin(angle), r * np.sin(angle) + tg * np.cos(angle)], 1))
    P = np.array(rows)
    m = grid(P, wrap_v=True, flip=True)
    tip = P[-1]
    return Mesh.merge([m, _cap(tip, tip.mean(0), np.array([0, math.cos(angle), math.sin(angle)]))])


def blade_row(x, r0, r1, count, chord, twist0, twist1, thick, phase=0.0, stations=4, camber=0.05):
    radii = np.linspace(r0, r1, stations)
    chords = np.linspace(chord[0], chord[1], stations) if np.ndim(chord) else [chord] * stations
    tw = np.linspace(twist0, twist1, stations)
    return Mesh.merge([blade(x, radii, chords, tw, thick, phase + TAU * k / count, camber=camber, n=8)
                       for k in range(count)])


def blob(center, axes, radii, nu=40, nv=28, bumps=None):
    """A deformed ellipsoid: `bumps(u, v)` returns a radius multiplier."""
    e = [np.asarray(a, float) for a in axes]
    u = np.linspace(0, TAU, nu, endpoint=False)[:, None]
    v = np.linspace(1e-3, math.pi - 1e-3, nv)[None, :]
    k = bumps(u, v) if bumps else 1.0
    d = (np.cos(u) * np.sin(v) * radii[0] * k)[..., None] * e[0] + (np.sin(u) * np.sin(v) * radii[1] * k)[..., None] * e[1] \
        + (np.cos(v) * radii[2] * k)[..., None] * e[2]
    P = np.asarray(center, float) + d
    m = grid(P, wrap_u=True, flip=False)
    return Mesh.merge([m, _cap(P[:, 0], P[:, 0].mean(0), e[2]), _cap(P[:, -1], P[:, -1].mean(0), -e[2])])


def hull2d(points):
    pts = sorted(map(tuple, np.round(points, 9)))
    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out
    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def belt_path(circles, n=160):
    """A closed belt around pulleys [(y, z, r), ...] in a plane: the convex hull, resampled."""
    pts = []
    for y, z, r in circles:
        a = np.linspace(0, TAU, 90, endpoint=False)
        pts.append(np.stack([y + r * np.cos(a), z + r * np.sin(a)], 1))
    h = hull2d(np.concatenate(pts))
    seg = np.linalg.norm(np.roll(h, -1, 0) - h, axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    t = np.linspace(0, s[-1], n, endpoint=False)
    closed = np.concatenate([h, h[:1]])
    return np.stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])], 1)


def rot(axis, angle):
    a = _unit(axis); c, s = math.cos(angle), math.sin(angle)
    x, y, z = a
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


class Part:
    def __init__(self, id, name, group, explode, **text):
        self.id, self.name, self.group, self.explode, self.text = id, name, group, explode, text
        self.by_material = {}

    def add(self, material, *meshes):
        self.by_material.setdefault(material, []).extend(meshes)
        return self


def to_glb(parts, materials, title, R=None):
    """glTF 2.0 binary: node k is parts[k] (one primitive per material), under a root node."""
    blob_ = bytearray()
    views, accessors, meshes, nodes, mats, mat_index = [], [], [], [], [], {}

    def view(data, target):
        while len(blob_) % 4:
            blob_.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob_), "byteLength": len(data), "target": target})
        blob_.extend(data)
        return len(views) - 1

    def material(name):
        if name not in mat_index:
            m = materials[name]
            spec = {"name": name, "doubleSided": True,
                    "pbrMetallicRoughness": {"baseColorFactor": list(m["color"]),
                                             "metallicFactor": m.get("metal", 0.0), "roughnessFactor": m.get("rough", 0.5)}}
            if m.get("emissive"):
                spec["emissiveFactor"] = list(m["emissive"])
            if m["color"][3] < 1:
                spec["alphaMode"] = "BLEND"
            mat_index[name] = len(mats); mats.append(spec)
        return mat_index[name]

    for part in parts:
        prims = []
        for mname, ms in part.by_material.items():
            m = Mesh.merge(ms)
            if R is not None:
                m = m.moved(R)
            pos, nrm = m.pos.astype(np.float32), m.nrm.astype(np.float32)
            idx = m.tris.astype(np.uint32).reshape(-1)
            a = len(accessors)
            accessors += [
                {"bufferView": view(pos.tobytes(), 34962), "componentType": 5126, "count": len(pos), "type": "VEC3",
                 "min": [float(v) for v in pos.min(0)], "max": [float(v) for v in pos.max(0)]},
                {"bufferView": view(nrm.tobytes(), 34962), "componentType": 5126, "count": len(nrm), "type": "VEC3"},
                {"bufferView": view(idx.tobytes(), 34963), "componentType": 5125, "count": len(idx), "type": "SCALAR"}]
            prims.append({"attributes": {"POSITION": a, "NORMAL": a + 1}, "indices": a + 2, "material": material(mname)})
        meshes.append({"name": part.name, "primitives": prims})
        nodes.append({"name": part.name, "mesh": len(meshes) - 1})
    nodes.append({"name": title, "children": list(range(len(parts)))})
    while len(blob_) % 4:
        blob_.append(0)
    doc = {"asset": {"version": "2.0", "generator": "Apex study library"}, "scene": 0,
           "scenes": [{"name": title, "nodes": [len(nodes) - 1]}], "nodes": nodes, "meshes": meshes,
           "materials": mats, "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob_)}]}
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob_)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(js), b"JSON") + js
            + struct.pack("<I4s", len(blob_), b"BIN\x00") + bytes(blob_))


def vertex_count(parts):
    return sum(len(m.pos) for p in parts for ms in p.by_material.values() for m in ms)
