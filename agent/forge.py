"""Forge — turning a thing Apex designed into a thing a machine can make.

The blueprint's Phase 12 success check is one sentence, and the whole phase
lives in one adjective:

    *A designed object reaches a validated **manufacturable** representation.*

Before this module Apex could design. `agent/blender_bridge.py` builds a
measured primitive in Blender and exports it, and `agent/assets.py` keeps every
export as an immutable version with the command that produced it. What came out
was `.glb` — a format for *looking* at things. Send a `.glb` to a printer and
nothing happens; it is the wrong file, in the wrong units, with no statement
about whether the geometry is even a solid.

So this module answers the three questions that stand between a mesh and a part:

1. **Is it a solid?** A watertight, consistently-wound, non-self-overlapping
   shell encloses a volume. An open shell does not, and a slicer handed one
   produces either nothing or a confident wrong answer.
2. **Can the machine make it?** Walls thinner than the nozzle cannot be
   extruded. Overhangs past the support angle fall. A part larger than the
   build volume does not fit.
3. **What size is it, really?** No mesh format in common use states its unit.
   STL has never had one. glTF's convention is metres, Blender's is whatever
   the scene says, and a `50` in a file is fifty of something nobody wrote
   down. The most common failure in desktop manufacturing is a part that
   arrives 25.4x or 1000x wrong, and it is a *silent* failure — the file is
   valid, the print succeeds, the object is useless.

## The rule this module exists to enforce: never pass by default

A validator that cannot measure something must say so. That is not a
formality here — it is the exact failure this codebase keeps finding: a check
that runs, finds nothing because it never really looked, and reports success.
So `validate()` has four outcomes per check, not two:

    ok        measured, within limits
    warn      measured, printable but needs attention (supports, say)
    fail      measured, outside limits
    unknown   NOT measured — too big to sample, no rays hit, unsupported input

and a report containing any `unknown` is `unverified`, never `manufacturable`.
`agent/capabilities.py` made the same choice for the same reason: the third
state is the entire point, and collapsing it to a boolean is how a subsystem
becomes indistinguishable from a working one.

## Why this does not need Blender

`primitive()` builds the same shapes `blender_bridge.SHAPES` names, in
millimetres, from `blender_bridge.SHAPE_DIMS` and `blender_bridge.validate_dims`
— one list, many consumers, so a shape added there and not here fails a test
rather than drifting. That means the whole Forge chain — design, validate,
export — runs with no Blender installed, which is also why every claim in this
file can be checked by the test suite rather than asserted.

Reading a real `.glb` is still supported (`read_glb`), because that is what
`board_create` actually produces, and a Forge that could not ingest Apex's own
output would be a demo.

## Units

Everything inside this module is **millimetres**, always, with no exceptions
and no per-call unit argument. Conversions happen exactly once, at the reader,
and the reader records what it assumed. A single internal unit is the only
defence against the failure in (3) above.
"""
from __future__ import annotations

import json
import math
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

# Two vertices closer than this are the same vertex. STL stores no topology at
# all — it is a bag of loose triangles — so every question about holes, winding
# or manifoldness starts by deciding which corners are shared. Real exporters
# write float32, so bit-identical corners are common but not guaranteed; a
# tolerance is unavoidable.
#
# Known limitation, stated rather than hidden: this is grid quantisation, so
# two points 1e-6mm apart can still land in different cells if they straddle a
# boundary. The alternative (a spatial tree with a true radius query) is a lot
# of machinery for a difference that does not arise in exporter output, where
# shared corners come from the same arithmetic.
WELD_TOLERANCE_MM = 1e-5

# A triangle with less area than this is degenerate: three collinear points, or
# two corners in the same place. It contributes nothing and its normal is
# meaningless (the cross product is ~zero, so its direction is noise).
DEGENERATE_AREA_MM2 = 1e-9

# Thickness is measured by casting rays, which costs rays x triangles. Past
# this many triangles the measurement is declined rather than run for minutes —
# and declining means `unknown`, which means `unverified`, not `ok`.
MAX_TRIANGLES_FOR_THICKNESS = 60000

# How many rays. Sampling is deterministic (an even stride through the triangle
# list, never a random draw) so the same mesh always produces the same report —
# a validator whose verdict wobbles between runs cannot be trusted or tested.
THICKNESS_RAY_BUDGET = 3000

# Ray batch size. Bounds peak memory at roughly
# batch x triangles x 3 x 8 bytes per working array.
_RAY_BATCH = 64

MM_PER_INCH = 25.4


class ForgeError(Exception):
    """A refusal or a broken input — always carries a message for a person."""


@dataclass(frozen=True)
class Mesh:
    """Triangles in millimetres: `tris` is (n, 3, 3) — n triangles, 3 corners,
    xyz. Deliberately the dumbest possible representation.

    `assumed` records what the reader had to *guess* to produce these numbers
    (glTF's metres, a file with no unit at all). It is carried into the report
    so that a wrong guess is visible in the output instead of silently scaling
    the part.
    """

    tris: np.ndarray
    source: str = ""
    assumed: str = ""

    def __post_init__(self):
        arr = np.asarray(self.tris, dtype=np.float64)
        if arr.ndim != 3 or arr.shape[1:] != (3, 3):
            raise ForgeError(
                f"a mesh must be (n, 3, 3) — n triangles of 3 xyz corners — "
                f"got {arr.shape}")
        if arr.size and not np.isfinite(arr).all():
            raise ForgeError("mesh contains NaN or infinite coordinates")
        object.__setattr__(self, "tris", arr)

    @property
    def count(self) -> int:
        return int(self.tris.shape[0])

    def bounds_mm(self) -> tuple[np.ndarray, np.ndarray]:
        """(min xyz, max xyz). Empty mesh gives zeros, not an exception —
        callers check `count`."""
        if not self.count:
            return np.zeros(3), np.zeros(3)
        flat = self.tris.reshape(-1, 3)
        return flat.min(axis=0), flat.max(axis=0)

    def size_mm(self) -> np.ndarray:
        lo, hi = self.bounds_mm()
        return hi - lo

    def area_mm2(self) -> float:
        """Total surface area. Uses |cross| / 2 per triangle."""
        if not self.count:
            return 0.0
        return float(np.linalg.norm(_cross_edges(self.tris), axis=1).sum() / 2.0)

    def volume_mm3(self) -> float:
        """Signed volume by the divergence theorem: the sum of the signed
        volumes of the tetrahedra from the origin to each triangle.

        SIGNED on purpose. For a closed shell with outward normals this is the
        enclosed volume and is positive; a shell wound inside-out gives the same
        magnitude with a minus sign, and that sign is the cheapest detector of
        an inverted mesh there is. `abs()` here would throw away the finding.
        """
        if not self.count:
            return 0.0
        v0, v1, v2 = self.tris[:, 0], self.tris[:, 1], self.tris[:, 2]
        return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)

    def normals(self) -> np.ndarray:
        """Unit face normals, (n, 3). Degenerate triangles get a zero vector
        rather than NaN — dividing by a zero-length cross product is how a
        single bad triangle poisons every downstream average."""
        c = _cross_edges(self.tris)
        n = np.linalg.norm(c, axis=1)
        out = np.zeros_like(c)
        good = n > 1e-300
        out[good] = c[good] / n[good, None]
        return out

    def translated(self, offset: Sequence[float]) -> "Mesh":
        return Mesh(self.tris + np.asarray(offset, dtype=np.float64),
                    source=self.source, assumed=self.assumed)

    def scaled(self, factor: float) -> "Mesh":
        return Mesh(self.tris * float(factor),
                    source=self.source, assumed=self.assumed)


def _cross_edges(tris: np.ndarray) -> np.ndarray:
    """(v1-v0) x (v2-v0) per triangle — twice the area vector."""
    return np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])


# --------------------------------------------------------------------------
# Welding and topology
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Topology:
    """What the triangle soup turns out to be once shared corners are found."""

    vertices: np.ndarray          # (v, 3)
    faces: np.ndarray             # (f, 3) indices into vertices
    boundary_edges: int           # used by exactly one triangle -> a hole
    nonmanifold_edges: int        # used by three or more -> not a solid
    flipped_edges: int            # used twice, both the same way -> bad winding
    degenerate_faces: int
    duplicate_faces: int

    @property
    def watertight(self) -> bool:
        return self.boundary_edges == 0 and self.nonmanifold_edges == 0

    @property
    def consistent_winding(self) -> bool:
        return self.flipped_edges == 0


def weld(mesh: Mesh, tol: float = WELD_TOLERANCE_MM) -> tuple[np.ndarray, np.ndarray]:
    """(vertices, faces) — the indexed form. Quantises to a `tol` grid.

    Every topological question below needs this first. It is also what the 3MF
    writer needs, since 3MF is indexed and STL is not; doing it once here means
    the exporter and the validator can never disagree about which corners are
    shared, which would otherwise be a real drift class (a file that validates
    as a solid and exports with holes).
    """
    if not mesh.count:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    flat = mesh.tris.reshape(-1, 3)
    keys = np.round(flat / float(tol)).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True,
                                  return_inverse=True)
    verts = flat[first]
    faces = np.asarray(inverse).reshape(-1, 3).astype(np.int64)
    return verts, faces


def topology(mesh: Mesh, tol: float = WELD_TOLERANCE_MM) -> Topology:
    """Count every way this mesh fails to be a closed, correctly-wound solid.

    The edge test is the load-bearing one. In a closed manifold surface each
    edge is shared by exactly two triangles, and those two traverse it in
    OPPOSITE directions. So:

      * used once   -> the surface has a hole there
      * used 3+     -> more than two faces meet at that edge; not a solid
      * used twice, same direction -> the two faces disagree about which side
        is out, which is an inverted normal and produces a part that slices
        inside-out

    Counting only the first of those — which is what "is it watertight" usually
    means in casual use — passes a mesh that is closed and inside-out.
    """
    verts, faces = weld(mesh, tol)
    if not len(faces):
        return Topology(verts, faces, 0, 0, 0, 0, 0)

    areas = np.linalg.norm(_cross_edges(mesh.tris), axis=1) / 2.0
    collapsed = ((faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) |
                 (faces[:, 0] == faces[:, 2]))
    degenerate = int(np.count_nonzero((areas < DEGENERATE_AREA_MM2) | collapsed))

    # Duplicate faces: the same three welded corners appearing twice, in any
    # winding. Two coincident triangles read as a closed edge pair to the test
    # below, so a mesh with duplicated faces can look watertight while being a
    # zero-volume double skin.
    sorted_faces = np.sort(faces, axis=1)
    _, counts = np.unique(sorted_faces, axis=0, return_counts=True)
    duplicate = int(counts[counts > 1].sum() - np.count_nonzero(counts > 1))

    # Directed edges, three per face, then grouped by their undirected key.
    live = faces[~collapsed]
    if not len(live):
        return Topology(verts, faces, 0, 0, 0, degenerate, duplicate)
    directed = np.concatenate([live[:, [0, 1]], live[:, [1, 2]], live[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, inv, counts = np.unique(undirected, axis=0, return_inverse=True,
                               return_counts=True)
    inv = np.asarray(inv).reshape(-1)

    boundary = int(np.count_nonzero(counts == 1))
    nonmanifold = int(np.count_nonzero(counts > 2))

    # For each undirected edge, how many of its uses run "low index -> high".
    # A correctly wound pair has exactly one. Two (or zero) means both faces
    # traverse it the same way.
    forward = (directed[:, 0] < directed[:, 1]).astype(np.int64)
    per_edge_forward = np.bincount(inv, weights=forward,
                                   minlength=len(counts)).astype(np.int64)
    paired = counts == 2
    flipped = int(np.count_nonzero(paired & (per_edge_forward != 1)))

    return Topology(verts, faces, boundary, nonmanifold, flipped,
                    degenerate, duplicate)


# --------------------------------------------------------------------------
# Measurements a machine cares about
# --------------------------------------------------------------------------

def thickness_samples(mesh: Mesh, max_rays: int = THICKNESS_RAY_BUDGET
                      ) -> tuple[Optional[np.ndarray], str]:
    """How thick is the material under each sampled point?

    Returns `(samples, "")`, or `(None, why)` when the measurement was not
    made. The reason travels with the refusal rather than being reconstructed
    by the caller: "too many triangles" and "no ray hit anything" are
    completely different facts about a mesh, and a single `None` that gets
    described as whichever the caller guessed is a lie in the report.

    The method is the standard one: from the centre of a face, walk INWARD
    along that face's own normal and find the first surface you hit. That
    distance is the wall thickness at that point, and it is what decides
    whether an extruder can lay the wall down at all.

    Two honest limitations, both of which push the answer the same way:

      * it measures perpendicular to the surface, so the true minimum thickness
        of a solid — which can lie between two surfaces that are not parallel —
        may be smaller than anything sampled here;
      * with more triangles than rays it samples a stride through the face
        list, so a single thin feature made of few triangles can be missed.

    Both mean this can **over**-report thickness, never under-report it. So a
    `fail` from this check is trustworthy and an `ok` is "nothing thin was
    found", which is why the report says `thinnest measured` rather than
    `thinnest`. Returning `None` (too many triangles, no rays landed) is a
    third answer and must not be read as either.
    """
    n = mesh.count
    if not n:
        return None, "the mesh has no triangles"
    if n > MAX_TRIANGLES_FOR_THICKNESS:
        return None, (f"{n} triangles is past the {MAX_TRIANGLES_FOR_THICKNESS} "
                      f"limit for ray sampling, which would take minutes")

    tris = mesh.tris
    normals = mesh.normals()
    live = np.linalg.norm(normals, axis=1) > 0.5
    idx = np.nonzero(live)[0]
    if not len(idx):
        return None, "every triangle is degenerate, so no face has a normal to follow"
    if len(idx) > max_rays:
        # Even stride, never random: the same mesh must always give the same
        # verdict, or the check cannot be tested and cannot be trusted.
        idx = idx[np.linspace(0, len(idx) - 1, max_rays).astype(np.int64)]

    diag = float(np.linalg.norm(mesh.size_mm())) or 1.0
    t_min = max(1e-9, diag * 1e-7)

    origins = tris[idx].mean(axis=1)
    directions = -normals[idx]

    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0

    out = np.full(len(idx), np.inf)
    for start in range(0, len(idx), _RAY_BATCH):
        o = origins[start:start + _RAY_BATCH]
        d = directions[start:start + _RAY_BATCH]
        # Moller-Trumbore, every ray against every triangle.
        h = np.cross(d[:, None, :], e2[None, :, :])
        a = np.einsum("rtk,tk->rt", h, e1)
        parallel = np.abs(a) < 1e-12
        a_safe = np.where(parallel, 1.0, a)
        f = 1.0 / a_safe
        s = o[:, None, :] - v0[None, :, :]
        u = f * np.einsum("rtk,rtk->rt", s, h)
        q = np.cross(s, e1[None, :, :])
        v = f * np.einsum("rk,rtk->rt", d, q)
        t = f * np.einsum("tk,rtk->rt", e2, q)
        hit = (~parallel) & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > t_min)
        t = np.where(hit, t, np.inf)
        out[start:start + _RAY_BATCH] = t.min(axis=1)

    finite = out[np.isfinite(out)]
    if not len(finite):
        return None, ("no ray cast inward from a face hit another surface — the "
                      "mesh is open, so there is no material to measure through")
    return finite, ""


def overhang_report(mesh: Mesh, max_angle_deg: float) -> tuple[float, float, float]:
    """(area needing support in mm2, total area, steepest overhang in degrees).

    The angle is measured from vertical, the way a slicer's "support overhang
    threshold" is: a vertical wall is 0 degrees and prints; a horizontal
    ceiling is 90 and must be held up. For a face with outward unit normal n,
    that angle is asin(-n_z) once the face points downward at all.

    The flat underside a part rests on is excluded. It is the steepest possible
    overhang by this measure and needs no support whatsoever, and a checker that
    flagged the bottom of every cube would be ignored within a day — which is
    the same as not having one.
    """
    if not mesh.count:
        return 0.0, 0.0, 0.0
    normals = mesh.normals()
    areas = np.linalg.norm(_cross_edges(mesh.tris), axis=1) / 2.0
    lo, _hi = mesh.bounds_mm()
    z_floor = lo[2]
    diag = float(np.linalg.norm(mesh.size_mm())) or 1.0
    on_plate = np.all(np.abs(mesh.tris[:, :, 2] - z_floor) <= diag * 1e-6, axis=1)

    down = np.clip(-normals[:, 2], -1.0, 1.0)
    angles = np.degrees(np.arcsin(np.clip(down, 0.0, 1.0)))
    needs = (down > 0) & (angles > float(max_angle_deg)) & (~on_plate)
    # max(0.0, ...) rather than the bare max: clip preserves -0.0, arcsin(-0.0)
    # is -0.0, and a report reading "steepest -0 degrees" is a bug report
    # waiting to happen.
    steepest = max(0.0, float(angles[~on_plate].max())) if np.any(~on_plate) else 0.0
    return float(areas[needs].sum()), float(areas.sum()), steepest


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"

MANUFACTURABLE = "manufacturable"
NOT_MANUFACTURABLE = "not_manufacturable"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Finding:
    check: str
    level: str
    message: str


@dataclass(frozen=True)
class Report:
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """`unknown` outranks `ok` deliberately.

        A report with an unmeasured check is `unverified`. It is NOT
        `manufacturable` with a footnote, because the entire failure mode this
        module was written against is a check that did not run being read as a
        check that passed.
        """
        levels = {f.level for f in self.findings}
        if FAIL in levels:
            return NOT_MANUFACTURABLE
        if UNKNOWN in levels:
            return UNVERIFIED
        return MANUFACTURABLE

    @property
    def ok(self) -> bool:
        return self.verdict == MANUFACTURABLE

    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == FAIL]

    def describe(self) -> str:
        """The whole report as text, for a person or for a tool result."""
        mark = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL", UNKNOWN: "????"}
        head = {
            MANUFACTURABLE: "MANUFACTURABLE",
            NOT_MANUFACTURABLE: "NOT MANUFACTURABLE",
            UNVERIFIED: "UNVERIFIED — something could not be measured",
        }[self.verdict]
        lines = [head, ""]
        s = self.stats
        if s:
            size = s.get("size_mm") or [0, 0, 0]
            lines.append(
                f"{s.get('triangles', 0)} triangles, "
                f"{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} mm "
                f"({size[0] / MM_PER_INCH:.3f} x {size[1] / MM_PER_INCH:.3f} x "
                f"{size[2] / MM_PER_INCH:.3f} in)")
            if s.get("assumed"):
                lines.append(f"unit assumed by the reader: {s['assumed']}")
            lines.append("")
        for f in self.findings:
            lines.append(f"  [{mark.get(f.level, '?')}] {f.check}: {f.message}")
        return "\n".join(lines)


def validate(mesh: Mesh, *, nozzle_mm: float = 0.4, min_wall_mm: Optional[float] = None,
             overhang_deg: float = 45.0,
             build_mm: Sequence[float] = (256.0, 256.0, 256.0),
             measure_thickness: bool = True) -> Report:
    """Every question between this mesh and a made part, each answered or declined.

    `min_wall_mm` defaults to two nozzle widths, which is the smallest wall a
    fused-filament machine can print with any strength at all — a single-width
    wall extrudes but tears.
    """
    if min_wall_mm is None:
        min_wall_mm = 2.0 * float(nozzle_mm)

    findings: list[Finding] = []
    size = mesh.size_mm()
    vol = mesh.volume_mm3()
    stats = {
        "triangles": mesh.count,
        "size_mm": [round(float(v), 4) for v in size],
        "volume_mm3": round(vol, 4),
        "area_mm2": round(mesh.area_mm2(), 4),
        "source": mesh.source,
        "assumed": mesh.assumed,
    }

    if not mesh.count:
        findings.append(Finding("geometry", FAIL, "the mesh has no triangles"))
        return Report(findings, stats)

    topo = topology(mesh)
    stats.update({
        "boundary_edges": topo.boundary_edges,
        "nonmanifold_edges": topo.nonmanifold_edges,
        "flipped_edges": topo.flipped_edges,
        "vertices": int(len(topo.vertices)),
    })

    if topo.boundary_edges or topo.nonmanifold_edges:
        bits = []
        if topo.boundary_edges:
            bits.append(f"{topo.boundary_edges} open edge(s) — the surface has holes")
        if topo.nonmanifold_edges:
            bits.append(f"{topo.nonmanifold_edges} edge(s) shared by 3+ faces")
        findings.append(Finding(
            "watertight", FAIL,
            "; ".join(bits) + ". This is a surface, not a solid, so there is no "
            "inside for a slicer to fill."))
    else:
        findings.append(Finding("watertight", OK,
                                "closed shell, every edge shared by exactly two faces"))

    if topo.flipped_edges:
        findings.append(Finding(
            "normals", FAIL,
            f"{topo.flipped_edges} edge(s) where both faces wind the same way — "
            f"part of the surface is inside out"))
    elif vol < 0:
        findings.append(Finding(
            "normals", FAIL,
            f"enclosed volume is negative ({vol:.2f} mm3) — the whole shell is "
            f"wound inside out. Flip the normals."))
    else:
        findings.append(Finding("normals", OK, "consistent, facing outward"))

    if topo.degenerate_faces:
        findings.append(Finding(
            "degenerate faces", WARN,
            f"{topo.degenerate_faces} triangle(s) with no area. Most slicers drop "
            f"them, but they carry no normal and can confuse repair tools."))
    if topo.duplicate_faces:
        findings.append(Finding(
            "duplicate faces", WARN,
            f"{topo.duplicate_faces} triangle(s) appear more than once"))

    if abs(vol) <= DEGENERATE_AREA_MM2:
        findings.append(Finding(
            "volume", FAIL,
            "encloses no volume — a zero-thickness sheet cannot be made. Give it "
            "a thickness first."))
    else:
        findings.append(Finding("volume", OK, f"{abs(vol) / 1000.0:.3f} cm3 of material"))

    bx, by, bz = (float(v) for v in build_mm)
    if size[0] > bx or size[1] > by or size[2] > bz:
        findings.append(Finding(
            "build volume", FAIL,
            f"{size[0]:.1f} x {size[1]:.1f} x {size[2]:.1f} mm does not fit a "
            f"{bx:.0f} x {by:.0f} x {bz:.0f} mm build volume"))
    else:
        findings.append(Finding("build volume", OK,
                                f"fits {bx:.0f} x {by:.0f} x {bz:.0f} mm"))

    # Units are never stated by the file, so the check is a plausibility one and
    # it says exactly what it assumed. See this module's docstring, point (3).
    longest = float(size.max())
    if longest < 1.0:
        findings.append(Finding(
            "scale", FAIL,
            f"the largest dimension is {longest:.4f} mm. That is almost certainly "
            f"a unit error — the same numbers read as metres would be "
            f"{longest * 1000:.1f} mm, as inches {longest * MM_PER_INCH:.1f} mm."))
    elif longest < 5.0:
        findings.append(Finding(
            "scale", WARN,
            f"the largest dimension is only {longest:.2f} mm. Intended, or a unit "
            f"mix-up? As inches it would be {longest * MM_PER_INCH:.1f} mm."))
    else:
        findings.append(Finding("scale", OK,
                                f"largest dimension {longest:.2f} mm "
                                f"({longest / MM_PER_INCH:.2f} in)"))

    if measure_thickness:
        samples, why = thickness_samples(mesh)
        if samples is None:
            findings.append(Finding(
                "wall thickness", UNKNOWN,
                f"not measured: {why}. Treat the wall thickness as unchecked."))
        else:
            thinnest = float(samples.min())
            stats["thinnest_measured_mm"] = round(thinnest, 4)
            if thinnest < float(min_wall_mm):
                findings.append(Finding(
                    "wall thickness", FAIL,
                    f"thinnest measured wall is {thinnest:.3f} mm, below the "
                    f"{float(min_wall_mm):.2f} mm minimum for a {float(nozzle_mm):.2f} mm "
                    f"nozzle. That wall cannot be extruded."))
            else:
                findings.append(Finding(
                    "wall thickness", OK,
                    f"thinnest measured wall {thinnest:.2f} mm "
                    f"(minimum {float(min_wall_mm):.2f} mm)"))
    else:
        findings.append(Finding("wall thickness", UNKNOWN, "not measured — asked to skip"))

    unsupported, total, steepest = overhang_report(mesh, overhang_deg)
    stats["overhang_area_mm2"] = round(unsupported, 3)
    stats["steepest_overhang_deg"] = round(steepest, 2)
    if unsupported > 0:
        pct = (unsupported / total * 100.0) if total else 0.0
        findings.append(Finding(
            "overhangs", WARN,
            f"{unsupported:.1f} mm2 ({pct:.1f}% of the surface) overhangs past "
            f"{float(overhang_deg):.0f} degrees, steepest {steepest:.0f}. Printable "
            f"with supports; it will not bridge unaided."))
    else:
        findings.append(Finding("overhangs", OK,
                                f"nothing past {float(overhang_deg):.0f} degrees "
                                f"(steepest {steepest:.0f})"))

    return Report(findings, stats)


# --------------------------------------------------------------------------
# Primitives — the same shapes blender_bridge knows, without Blender
# --------------------------------------------------------------------------
#
# Every shape rests ON the build plate: minimum z is 0, centred in x and y.
# That is not decoration. The overhang check has to know which face the part
# stands on, and "the lowest one" is only true if the part is actually sitting
# down. A primitive floating around the origin would report its own base as a
# 90-degree overhang.

CIRCLE_SEGMENTS = 48
SPHERE_SEGMENTS = 32
SPHERE_RINGS = 16
TORUS_MAJOR_SEGMENTS = 48
TORUS_MINOR_SEGMENTS = 24


def _mesh_from(verts: list, faces: list, source: str) -> Mesh:
    v = np.asarray(verts, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    return Mesh(v[f], source=source, assumed="millimetres, built here")


def _cube(w: float, d: float, h: float) -> Mesh:
    x0, x1 = -w / 2.0, w / 2.0
    y0, y1 = -d / 2.0, d / 2.0
    verts = [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0),
             (x0, y0, h), (x1, y0, h), (x1, y1, h), (x0, y1, h)]
    faces = [(0, 3, 2), (0, 2, 1),          # bottom, -z
             (4, 5, 6), (4, 6, 7),          # top, +z
             (0, 1, 5), (0, 5, 4),          # -y
             (1, 2, 6), (1, 6, 5),          # +x
             (2, 3, 7), (2, 7, 6),          # +y
             (3, 0, 4), (3, 4, 7)]          # -x
    return _mesh_from(verts, faces, "cube")


def _plane(w: float, d: float) -> Mesh:
    x0, x1 = -w / 2.0, w / 2.0
    y0, y1 = -d / 2.0, d / 2.0
    verts = [(x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)]
    return _mesh_from(verts, [(0, 1, 2), (0, 2, 3)], "plane")


def _cylinder(diameter: float, h: float, seg: int = CIRCLE_SEGMENTS) -> Mesh:
    r = diameter / 2.0
    ring = [(r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg))
            for i in range(seg)]
    verts = [(x, y, 0.0) for x, y in ring] + [(x, y, h) for x, y in ring]
    verts.append((0.0, 0.0, 0.0))   # bottom centre, index 2*seg
    verts.append((0.0, 0.0, h))     # top centre,    index 2*seg + 1
    bc, tc = 2 * seg, 2 * seg + 1
    faces = []
    for i in range(seg):
        j = (i + 1) % seg
        faces.append((i, j, seg + j))
        faces.append((i, seg + j, seg + i))
        faces.append((bc, j, i))
        faces.append((tc, seg + i, seg + j))
    return _mesh_from(verts, faces, "cylinder")


def _cone(diameter: float, h: float, seg: int = CIRCLE_SEGMENTS) -> Mesh:
    r = diameter / 2.0
    verts = [(r * math.cos(2 * math.pi * i / seg), r * math.sin(2 * math.pi * i / seg), 0.0)
             for i in range(seg)]
    verts.append((0.0, 0.0, 0.0))   # base centre, index seg
    verts.append((0.0, 0.0, h))     # apex,        index seg + 1
    base_c, apex = seg, seg + 1
    faces = []
    for i in range(seg):
        j = (i + 1) % seg
        faces.append((i, j, apex))
        faces.append((base_c, j, i))
    return _mesh_from(verts, faces, "cone")


def _sphere(diameter: float, seg: int = SPHERE_SEGMENTS,
            rings: int = SPHERE_RINGS) -> Mesh:
    r = diameter / 2.0
    verts = []
    for i in range(rings + 1):
        phi = math.pi * i / rings
        for j in range(seg):
            theta = 2 * math.pi * j / seg
            verts.append((r * math.sin(phi) * math.cos(theta),
                          r * math.sin(phi) * math.sin(theta),
                          r * math.cos(phi) + r))
    def vid(i, j):
        return i * seg + (j % seg)
    faces = []
    for i in range(rings):
        for j in range(seg):
            a, b = vid(i, j), vid(i, j + 1)
            c, d = vid(i + 1, j), vid(i + 1, j + 1)
            # At the poles every vertex of the cap ring is the same point, so
            # one of the two triangles collapses. Skipping it here keeps the
            # degenerate-face count honest instead of shipping 2*seg zero-area
            # triangles in every sphere.
            if i != 0:
                faces.append((a, d, b))
            if i != rings - 1:
                faces.append((a, c, d))
    return _mesh_from(verts, faces, "sphere")


# `diameter` is the MAJOR diameter — the circle through the middle of the tube,
# matching Blender's Major Radius, which is what blender_bridge sends. A torus
# asked for as 60/20 therefore measures 80mm across overall, and the report's
# bounding box says so rather than leaving it to be discovered on the plate.
def _torus(diameter: float, tube_diameter: float,
           major: int = TORUS_MAJOR_SEGMENTS, minor: int = TORUS_MINOR_SEGMENTS) -> Mesh:
    R = diameter / 2.0
    rt = tube_diameter / 2.0
    verts = []
    for i in range(major):
        u = 2 * math.pi * i / major
        for j in range(minor):
            v = 2 * math.pi * j / minor
            verts.append(((R + rt * math.cos(v)) * math.cos(u),
                          (R + rt * math.cos(v)) * math.sin(u),
                          rt * math.sin(v) + rt))
    def vid(i, j):
        return (i % major) * minor + (j % minor)
    faces = []
    for i in range(major):
        for j in range(minor):
            a, b = vid(i, j), vid(i + 1, j)
            c, d = vid(i + 1, j + 1), vid(i, j + 1)
            faces.append((a, b, d))
            faces.append((b, c, d))
    return _mesh_from(verts, faces, "torus")


# One builder per shape blender_bridge knows about. `tests/test_forge.py`
# asserts this covers `blender_bridge.SHAPES` exactly — a shape added there and
# forgotten here would otherwise be designable and silently unmakeable, which
# is the drift `agent/schema.py` was written to kill in a different subsystem.
_BUILDERS = {
    "cube": lambda d: _cube(d["width"], d["depth"], d["height"]),
    "plane": lambda d: _plane(d["width"], d["depth"]),
    "sphere": lambda d: _sphere(d["diameter"]),
    "cylinder": lambda d: _cylinder(d["diameter"], d["height"]),
    "cone": lambda d: _cone(d["diameter"], d["height"]),
    "torus": lambda d: _torus(d["diameter"], d["tube_diameter"]),
}


def primitive(shape: str, dims_mm: dict) -> Mesh:
    """A measured primitive in millimetres, validated by blender_bridge's own rules.

    The validation is deliberately not reimplemented. `validate_dims` already
    decides what a cylinder needs and what counts as a sane number, and having
    two answers to that question is how "Apex made it but the printer refused"
    happens.
    """
    from agent import blender_bridge as _bb
    key = (shape or "").strip().lower()
    dims, reason = _bb.validate_dims(key, dims_mm or {})
    if dims is None:
        raise ForgeError(reason)
    builder = _BUILDERS.get(key)
    if builder is None:
        raise ForgeError(
            f"'{key}' is a shape Apex can design but not yet build a "
            f"manufacturable mesh for.")
    return builder(dims)


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------

_STL_HEADER = 80
_STL_TRI_BYTES = 50


def read_stl(path) -> Mesh:
    """Read binary or ASCII STL. The unit is assumed to be millimetres.

    That assumption is not a shortcut — **STL has no unit field and never
    has**. Every STL in the world is a pile of numbers whose meaning lives in
    an email. Millimetres is the near-universal convention for 3D printing, and
    recording the assumption on the Mesh means the report states it out loud
    instead of the part quietly arriving 25.4x wrong.

    Binary is detected by arithmetic, not by the leading word: plenty of binary
    writers put "solid" in the 80-byte header, so a file is binary when its
    length is exactly 84 + 50n for the n written at byte 80.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ForgeError(f"could not read {p.name}: {e}")
    if len(raw) < _STL_HEADER + 4:
        raise ForgeError(f"{p.name} is too short to be an STL file")

    count = struct.unpack_from("<I", raw, _STL_HEADER)[0]
    if len(raw) == _STL_HEADER + 4 + count * _STL_TRI_BYTES:
        return _read_stl_binary(raw, count, p.name)
    return _read_stl_ascii(raw, p.name)


def _read_stl_binary(raw: bytes, count: int, name: str) -> Mesh:
    if not count:
        raise ForgeError(f"{name} declares zero triangles")
    body = np.frombuffer(raw, dtype=np.uint8,
                         offset=_STL_HEADER + 4,
                         count=count * _STL_TRI_BYTES).reshape(count, _STL_TRI_BYTES)
    # Bytes 0..12 are the stored face normal, which is discarded on purpose:
    # it is routinely wrong or zero in files from real tools, and every normal
    # this module uses is recomputed from the winding, which cannot disagree
    # with the geometry it came from.
    verts = body[:, 12:48].copy().view("<f4").reshape(count, 3, 3)
    return Mesh(verts.astype(np.float64), source=name,
                assumed="millimetres (STL states no unit)")


def _read_stl_ascii(raw: bytes, name: str) -> Mesh:
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        raise ForgeError(f"{name} is neither valid binary STL nor readable text")
    verts: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                raise ForgeError(f"{name}: unreadable vertex line: {line.strip()!r}")
    if not verts:
        raise ForgeError(
            f"{name} has no vertices. If it is binary STL, its length does not "
            f"match its triangle count — the file is truncated or corrupt.")
    if len(verts) % 3:
        raise ForgeError(
            f"{name} has {len(verts)} vertices, which is not a whole number of "
            f"triangles — the file is truncated.")
    arr = np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
    return Mesh(arr, source=name, assumed="millimetres (STL states no unit)")


_GLTF_COMPONENTS = {5120: "<i1", 5121: "<u1", 5122: "<i2",
                    5123: "<u2", 5125: "<u4", 5126: "<f4"}
_GLTF_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path, unit_scale_mm: float = 1000.0, y_up: bool = True) -> Mesh:
    """Read a binary glTF — the format `board_create` actually produces.

    Two conversions happen here and both are assumptions worth stating:

    * **Scale.** glTF's convention is metres, so a 50mm cube is stored as
      `0.05`. Multiplying by 1000 gets back to millimetres. A file authored in
      some other unit will come out wrong, which is exactly why the validator
      prints the bounding box in both mm and inches — a wrong assumption is
      then visible in one line rather than at the end of a four-hour print.
    * **Axes.** glTF is Y-up; Blender, and this module, are Z-up. Blender's
      exporter maps (x, y, z) to (x, z, -y), so the inverse applied here is
      (X, Y, Z) -> (X, -Z, Y).

    Node transforms are composed down the scene tree, because a `board_create`
    object can carry its size in a node scale rather than in the vertices, and
    reading raw vertex data would silently produce a unit-sized part.
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ForgeError(f"could not read {p.name}: {e}")
    if len(raw) < 12 or raw[:4] != b"glTF":
        raise ForgeError(f"{p.name} is not a binary glTF (.glb) file")

    gltf, bin_chunk = _glb_chunks(raw, p.name)

    used = set(gltf.get("extensionsRequired") or [])
    if used:
        raise ForgeError(
            f"{p.name} requires the glTF extension(s) {', '.join(sorted(used))}, "
            f"which Apex cannot decode. Compressed geometry (Draco in "
            f"particular) would read as garbage rather than fail, so this is a "
            f"refusal instead. Re-export without compression.")

    tris: list[np.ndarray] = []
    for node_index, matrix in _glb_nodes(gltf):
        node = gltf["nodes"][node_index]
        mesh_index = node.get("mesh")
        if mesh_index is None:
            continue
        for prim in gltf["meshes"][mesh_index].get("primitives", []):
            if prim.get("mode", 4) != 4:
                continue        # points and lines are not parts
            pos_accessor = (prim.get("attributes") or {}).get("POSITION")
            if pos_accessor is None:
                continue
            pts = _glb_accessor(gltf, bin_chunk, pos_accessor, p.name)
            idx_accessor = prim.get("indices")
            if idx_accessor is None:
                faces = np.arange(len(pts), dtype=np.int64).reshape(-1, 3)
            else:
                faces = _glb_accessor(gltf, bin_chunk, idx_accessor,
                                      p.name).reshape(-1).astype(np.int64)
                if faces.size % 3:
                    raise ForgeError(f"{p.name}: index count is not a multiple of 3")
                faces = faces.reshape(-1, 3)
            homo = np.concatenate([pts, np.ones((len(pts), 1))], axis=1)
            world = (homo @ matrix.T)[:, :3]
            tris.append(world[faces])

    if not tris:
        raise ForgeError(
            f"{p.name} contains no triangles. It may hold only lines, points or "
            f"cameras — there is nothing here to make.")

    arr = np.concatenate(tris, axis=0) * float(unit_scale_mm)
    if y_up:
        arr = np.stack([arr[..., 0], -arr[..., 2], arr[..., 1]], axis=-1)
    note = f"glTF read as metres x{unit_scale_mm:g} -> mm"
    if y_up:
        note += ", Y-up converted to Z-up"
    return Mesh(arr, source=p.name, assumed=note)


def _glb_chunks(raw: bytes, name: str) -> tuple[dict, bytes]:
    total = struct.unpack_from("<I", raw, 8)[0]
    if total > len(raw):
        raise ForgeError(f"{name} declares {total} bytes but is {len(raw)} — truncated")
    offset, gltf, bin_chunk = 12, None, b""
    while offset + 8 <= total:
        clen, ctype = struct.unpack_from("<II", raw, offset)
        data = raw[offset + 8:offset + 8 + clen]
        if len(data) < clen:
            raise ForgeError(f"{name}: a chunk is truncated")
        if ctype == 0x4E4F534A:          # 'JSON'
            try:
                gltf = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as e:
                raise ForgeError(f"{name}: the JSON chunk is unreadable ({e})")
        elif ctype == 0x004E4942:        # 'BIN\0'
            bin_chunk = data
        offset += 8 + clen + ((4 - clen % 4) % 4)
    if gltf is None:
        raise ForgeError(f"{name} has no glTF JSON chunk")
    return gltf, bin_chunk


def _glb_nodes(gltf: dict):
    """Yield (node index, 4x4 world matrix) for every node, transforms composed.

    Iterative rather than recursive, and it refuses to revisit a node: a glTF
    scene graph is meant to be a tree, but a file with a cycle would otherwise
    spin here forever rather than report a broken file.
    """
    nodes = gltf.get("nodes") or []
    scenes = gltf.get("scenes") or []
    scene = gltf.get("scene", 0)
    if scenes and 0 <= scene < len(scenes):
        roots = list(scenes[scene].get("nodes") or [])
    else:
        children = {c for n in nodes for c in (n.get("children") or [])}
        roots = [i for i in range(len(nodes)) if i not in children]

    seen: set[int] = set()
    stack = [(i, np.eye(4)) for i in reversed(roots)]
    while stack:
        i, parent = stack.pop()
        if i in seen or not (0 <= i < len(nodes)):
            continue
        seen.add(i)
        world = parent @ _glb_local_matrix(nodes[i])
        yield i, world
        for c in reversed(nodes[i].get("children") or []):
            stack.append((c, world))


def _glb_local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        # glTF matrices are column-major; numpy reads row-major.
        return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    if "scale" in node:
        m = np.diag(list(node["scale"]) + [1.0]) @ m
    if "rotation" in node:
        x, y, z, w = (float(v) for v in node["rotation"])
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0],
            [0.0, 0.0, 0.0, 1.0]])
        m = r @ m
    if "translation" in node:
        t = np.eye(4)
        t[:3, 3] = node["translation"]
        m = t @ m
    return m


def _glb_accessor(gltf: dict, bin_chunk: bytes, index: int, name: str) -> np.ndarray:
    try:
        acc = gltf["accessors"][index]
    except (KeyError, IndexError):
        raise ForgeError(f"{name}: accessor {index} is missing")
    if "sparse" in acc:
        raise ForgeError(
            f"{name}: accessor {index} is sparse, which Apex does not decode. "
            f"Reading the base data alone would give the wrong geometry quietly.")
    dtype = _GLTF_COMPONENTS.get(acc.get("componentType"))
    if dtype is None:
        raise ForgeError(f"{name}: unknown componentType {acc.get('componentType')}")
    per = _GLTF_COUNTS.get(acc.get("type"))
    if per is None:
        raise ForgeError(f"{name}: unknown accessor type {acc.get('type')!r}")
    count = int(acc.get("count", 0))
    itemsize = np.dtype(dtype).itemsize

    bv_index = acc.get("bufferView")
    if bv_index is None:
        return np.zeros((count, per), dtype=np.float64)
    bv = gltf["bufferViews"][bv_index]
    buf = gltf["buffers"][bv.get("buffer", 0)]
    if buf.get("uri"):
        raise ForgeError(
            f"{name}: its geometry lives in a separate file ({buf['uri'][:40]}), "
            f"not inside the .glb. Export as a single self-contained .glb.")
    start = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    stride = int(bv.get("byteStride", 0)) or per * itemsize
    need = start + (count - 1) * stride + per * itemsize if count else start
    if need > len(bin_chunk):
        raise ForgeError(f"{name}: accessor {index} reads past the end of the buffer")
    raw = np.frombuffer(bin_chunk, dtype=np.uint8, offset=start,
                        count=(count - 1) * stride + per * itemsize if count else 0)
    if count:
        rows = np.lib.stride_tricks.as_strided(
            raw, shape=(count, per * itemsize), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dtype).reshape(count, per)
    else:
        out = np.zeros((0, per), dtype=dtype)
    return out.astype(np.float64) if dtype == "<f4" else out


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

STL = "stl"
THREEMF = "3mf"
FORMATS = (STL, THREEMF)


def write_stl(mesh: Mesh, path) -> Path:
    """Binary STL. Normals are recomputed from the winding, never carried over.

    STL is written because every machine on earth eats it, not because it is
    good: no units, no colour, no shared vertices, and a normal per facet that
    is free to contradict the triangle it belongs to. `write_3mf` is the better
    file and `export()` says so.
    """
    p = Path(path)
    n = mesh.count
    if not n:
        raise ForgeError("nothing to write — the mesh has no triangles")
    normals = mesh.normals().astype("<f4")
    verts = mesh.tris.astype("<f4")
    body = np.zeros((n, _STL_TRI_BYTES), dtype=np.uint8)
    body[:, 0:12] = normals.copy().view(np.uint8).reshape(n, 12)
    body[:, 12:48] = verts.copy().view(np.uint8).reshape(n, 36)
    header = b"Apex Forge - millimetres".ljust(_STL_HEADER, b"\0")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            fh.write(header)
            fh.write(struct.pack("<I", n))
            fh.write(body.tobytes())
    except OSError as e:
        raise ForgeError(f"could not write {p.name}: {e}")
    return p


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.'
    '3dmanufacturing-3dmodel+xml"/>'
    '</Types>\n')

_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    '</Relationships>\n')


def write_3mf(mesh: Mesh, path, title: str = "") -> Path:
    """3MF — the format that carries its own unit.

    This is the one that matters. A 3MF package states `unit="millimeter"` in
    its model XML, so the number 50 in the file means fifty millimetres to
    every reader, forever. Everything upstream of here has to assume; this is
    the first point in the chain where the unit stops being a convention and
    becomes part of the data.

    It is also indexed, so it shares `weld()` with the validator — the file
    that ships and the topology that was checked come from the same vertex
    merge, and cannot disagree about which corners are joined.
    """
    p = Path(path)
    if not mesh.count:
        raise ForgeError("nothing to write — the mesh has no triangles")
    verts, faces = weld(mesh)

    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n']
    if title:
        parts.append('<metadata name="Title">' + _xml_escape(title) + '</metadata>\n')
    parts.append('<metadata name="Application">Apex Forge</metadata>\n')
    parts.append('<resources><object id="1" type="model"><mesh><vertices>')
    parts.extend(f'<vertex x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>'
                 for x, y, z in verts)
    parts.append('</vertices><triangles>')
    parts.extend(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    parts.append('</triangles></mesh></object></resources>'
                 '<build><item objectid="1"/></build></model>\n')

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", _CONTENT_TYPES)
            z.writestr("_rels/.rels", _RELS)
            z.writestr("3D/3dmodel.model", "".join(parts))
    except OSError as e:
        raise ForgeError(f"could not write {p.name}: {e}")
    return p


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def read_3mf(path) -> Mesh:
    """Read back a 3MF. Exists so the writer can be checked against a reader
    rather than against my belief about the format — a round trip that recovers
    the same volume is evidence; a file that merely opens without error is not.
    """
    p = Path(path)
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("3D/3dmodel.model").decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        raise ForgeError(f"could not read {p.name} as 3MF: {e}")
    import re as _re
    if 'unit="millimeter"' not in xml:
        m = _re.search(r'<model[^>]*\bunit="([^"]+)"', xml)
        raise ForgeError(
            f"{p.name} declares unit {m.group(1)!r} rather than millimeter"
            if m else f"{p.name} declares no unit")
    verts = np.asarray(
        [(float(a), float(b), float(c)) for a, b, c in _re.findall(
            r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"\s*/>', xml)],
        dtype=np.float64)
    faces = np.asarray(
        [(int(a), int(b), int(c)) for a, b, c in _re.findall(
            r'<triangle v1="([^"]+)" v2="([^"]+)" v3="([^"]+)"\s*/>', xml)],
        dtype=np.int64)
    if not len(verts) or not len(faces):
        raise ForgeError(f"{p.name} has no mesh data")
    return Mesh(verts[faces], source=p.name, assumed="millimetres (3MF states it)")


READERS = {".stl": read_stl, ".glb": read_glb, ".3mf": read_3mf}


def read_any(path) -> Mesh:
    """Read whatever it is, by extension. An unknown extension is a refusal,
    never a guess: the formats differ enough that sniffing a wrong one produces
    a plausible mesh rather than an error."""
    p = Path(path)
    reader = READERS.get(p.suffix.lower())
    if reader is None:
        raise ForgeError(
            f"Apex cannot read '{p.suffix}'. It reads "
            f"{', '.join(sorted(READERS))}.")
    return reader(p)


def export(mesh: Mesh, path, *, fmt: str = THREEMF, title: str = "",
           report: Optional[Report] = None, force: bool = False) -> tuple[Path, Report]:
    """Validate, then write — and by default do not write what cannot be made.

    This ordering is the phase. "Reaches a validated manufacturable
    representation" is not two independent steps that happen to both occur; a
    Forge that exports first and validates afterwards produces files whose
    verdict nobody reads. The refusal is the feature.

    `force=True` exists because a human may legitimately want the file anyway —
    to repair it elsewhere, to look at it. It writes, and returns the failing
    report with it, so nothing downstream can mistake it for a clean part.
    """
    fmt = (fmt or "").strip().lower().lstrip(".")
    if fmt not in FORMATS:
        raise ForgeError(f"unknown format '{fmt}'. Choose {' or '.join(FORMATS)}.")
    rep = report if report is not None else validate(mesh)
    if not rep.ok and not force:
        reasons = "; ".join(f.message for f in rep.failures()) or rep.verdict
        raise ForgeError(
            f"refusing to export: {rep.verdict}. {reasons}"
            if rep.failures() else
            f"refusing to export: {rep.verdict} — a check could not be run, so "
            f"this file is not known to be makeable. "
            + "; ".join(f.message for f in rep.findings if f.level == UNKNOWN))
    p = Path(path)
    if fmt == STL:
        write_stl(mesh, p)
    else:
        write_3mf(mesh, p, title=title)
    return p, rep
