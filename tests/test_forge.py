"""Forge — does a mesh actually reach a *validated manufacturable* form.

Phase 12's success check hinges on one adjective, so these tests are built
around a single question: **would this check still pass if the thing it claims
to verify were removed?** Several times while writing this module the answer
was yes, and those are called out in the tests below by name.

The hollow-box fixtures matter more than they look. A solid primitive passes
every check trivially — it is thick everywhere, closed everywhere, and proves
almost nothing. A box with a 0.3mm wall is closed, correctly wound, positive
volume, fits the plate, and is *still impossible to print*. That is the mesh
that separates a wall-thickness check from a decoration.
"""
from __future__ import annotations

import json
import math
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from agent import forge


# --------------------------------------------------------------------------
# Fixtures: meshes whose right answer is known before the code runs
# --------------------------------------------------------------------------

def cube_mesh(w=20.0, d=20.0, h=20.0, origin=(0.0, 0.0, 0.0), flip=False) -> forge.Mesh:
    """An axis-aligned box with outward normals, its low corner at `origin`."""
    ox, oy, oz = origin
    x0, x1 = ox, ox + w
    y0, y1 = oy, oy + d
    z0, z1 = oz, oz + h
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
         (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
         (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    if flip:
        f = [(a, c, b) for a, b, c in f]
    arr = np.asarray(v, dtype=np.float64)[np.asarray(f, dtype=np.int64)]
    return forge.Mesh(arr)


def hollow_box(outer=20.0, wall=2.0) -> forge.Mesh:
    """A box with a sealed cavity — the only fixture that can fail a wall check.

    The cavity's surface is wound INWARD. A normal points away from material;
    for the outer shell that is outwards, and for a void inside a solid it is
    into the void. Get this backwards and the signed volume comes out as the
    sum of the two boxes rather than the difference, which is itself a useful
    assertion below.
    """
    outer_mesh = cube_mesh(outer, outer, outer)
    inner = outer - 2 * wall
    cavity = cube_mesh(inner, inner, inner, origin=(wall, wall, wall), flip=True)
    return forge.Mesh(np.concatenate([outer_mesh.tris, cavity.tris]))


def open_box() -> forge.Mesh:
    """A cube missing its top: closed except for one square hole."""
    tris = cube_mesh().tris
    return forge.Mesh(np.delete(tris, [2, 3], axis=0))


def ramp(overhang_deg: float, size: float = 20.0) -> forge.Mesh:
    """A closed wedge whose underside overhangs by exactly `overhang_deg`.

    Overhang is measured from VERTICAL, the way a slicer's support threshold
    is: a vertical wall is 0 and prints unaided, a flat ceiling is 90 and
    cannot. A face whose plane sits `phi` degrees from horizontal therefore
    overhangs by `90 - phi`, which is why the rise below is
    `tan(90 - overhang)` and not `tan(overhang)` — the first version of this
    fixture had it the other way round and quietly tested the complement of
    what it named.
    """
    rise = size * math.tan(math.radians(90.0 - float(overhang_deg)))
    v = [(0.0, 0.0, 0.0), (size, 0.0, rise), (size, 0.0, rise + size), (0.0, 0.0, size),
         (0.0, 10.0, 0.0), (size, 10.0, rise), (size, 10.0, rise + size), (0.0, 10.0, size)]
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
         (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
         (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    arr = np.asarray(v, dtype=np.float64)[np.asarray(f, dtype=np.int64)]
    return forge.Mesh(arr)


def build_glb(positions, indices, node=None, stride=None) -> bytes:
    """A real .glb, assembled from the spec: 12-byte header, JSON chunk, BIN chunk.

    Positions are glTF-native — Y-up, metres — so a test can state a size in
    glTF terms and assert the millimetres that come out, which is the whole
    point of reading one of these at all.
    """
    pos = np.asarray(positions, dtype="<f4")
    idx = np.asarray(indices, dtype="<u2").reshape(-1)
    if stride:
        rows = np.zeros((len(pos), stride), dtype=np.uint8)
        rows[:, :12] = pos.copy().view(np.uint8).reshape(len(pos), 12)
        pos_bytes = rows.tobytes()
    else:
        pos_bytes = pos.tobytes()
    pad = (4 - len(pos_bytes) % 4) % 4
    idx_off = len(pos_bytes) + pad
    blob = pos_bytes + b"\0" * pad + idx.tobytes()
    blob += b"\0" * ((4 - len(blob) % 4) % 4)

    view0 = {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes)}
    if stride:
        view0["byteStride"] = stride
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [node if node is not None else {"mesh": 0}],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": len(idx), "type": "SCALAR"}],
        "bufferViews": [view0,
                        {"buffer": 0, "byteOffset": idx_off,
                         "byteLength": len(idx.tobytes())}],
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(gltf).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total)
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


GLTF_BOX_50x40x30 = (
    # A box 0.05 wide (X), 0.03 tall (glTF Y = up), 0.04 deep (glTF Z).
    # After the reader's metres->mm and Y-up->Z-up conversions that must
    # measure 50 x 40 x 30 mm. Written out by hand so the expected answer does
    # not come from the same code that produces it.
    [(0, 0, 0), (0.05, 0, 0), (0.05, 0, 0.04), (0, 0, 0.04),
     (0, 0.03, 0), (0.05, 0.03, 0), (0.05, 0.03, 0.04), (0, 0.03, 0.04)],
    [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
     0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
     2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7],
)


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

class TestEveryDesignableShapeIsAlsoMakeable:
    """`blender_bridge` decides what Apex can design. If the two lists drift,
    a shape becomes designable and silently unmakeable — the same failure class
    `agent/schema.py` exists to prevent between two init lists."""

    def test_every_blender_shape_has_a_mesh_builder(self):
        from agent import blender_bridge
        assert set(forge._BUILDERS) == set(blender_bridge.SHAPES)

    def test_dimension_rules_are_not_reimplemented(self):
        # The refusal must be blender_bridge's own words, not a second opinion.
        from agent import blender_bridge
        _, expected = blender_bridge.validate_dims("cylinder", {"diameter": 10})
        with pytest.raises(forge.ForgeError) as e:
            forge.primitive("cylinder", {"diameter": 10})
        assert str(e.value) == expected

    def test_an_absurd_dimension_is_refused_not_built(self):
        with pytest.raises(forge.ForgeError):
            forge.primitive("cube", {"width": 1e9, "depth": 10, "height": 10})

    @pytest.mark.parametrize("shape,dims", [
        ("cube", {"width": 50, "depth": 40, "height": 30}),
        ("cylinder", {"diameter": 40, "height": 60}),
        ("cone", {"diameter": 40, "height": 60}),
        ("sphere", {"diameter": 40}),
        ("torus", {"diameter": 60, "tube_diameter": 20}),
    ])
    def test_solid_primitives_are_closed_correctly_wound_solids(self, shape, dims):
        mesh = forge.primitive(shape, dims)
        topo = forge.topology(mesh)
        assert topo.watertight, f"{shape} has {topo.boundary_edges} open edges"
        assert topo.consistent_winding
        assert topo.degenerate_faces == 0
        assert topo.duplicate_faces == 0
        assert mesh.volume_mm3() > 0, f"{shape} is wound inside out"

    @pytest.mark.parametrize("shape,dims,expected", [
        ("cube", {"width": 50, "depth": 40, "height": 30}, 50 * 40 * 30),
        ("cylinder", {"diameter": 40, "height": 60}, math.pi * 20 ** 2 * 60),
        ("cone", {"diameter": 40, "height": 60}, math.pi * 20 ** 2 * 60 / 3),
        ("sphere", {"diameter": 40}, 4 / 3 * math.pi * 20 ** 3),
        ("torus", {"diameter": 60, "tube_diameter": 20}, 2 * math.pi ** 2 * 30 * 10 ** 2),
    ])
    def test_volumes_match_the_arithmetic(self, shape, dims, expected):
        """Within faceting error, and always UNDER: a polygon inscribed in a
        circle encloses slightly less than the circle. A volume that came out
        over the analytic answer would mean the winding or the cap geometry is
        wrong in a way the topology checks cannot see."""
        vol = forge.primitive(shape, dims).volume_mm3()
        assert vol <= expected * 1.0001
        assert vol == pytest.approx(expected, rel=0.02)

    @pytest.mark.parametrize("shape,dims", [
        ("cube", {"width": 10, "depth": 10, "height": 10}),
        ("cylinder", {"diameter": 10, "height": 10}),
        ("cone", {"diameter": 10, "height": 10}),
        ("sphere", {"diameter": 10}),
        ("torus", {"diameter": 30, "tube_diameter": 10}),
        ("plane", {"width": 10, "depth": 10}),
    ])
    def test_everything_rests_on_the_build_plate(self, shape, dims):
        """Not cosmetic. The overhang check excludes the face a part stands on,
        identified as the lowest one — which is only the right face if the part
        is actually standing down."""
        lo, _ = forge.primitive(shape, dims).bounds_mm()
        assert lo[2] == pytest.approx(0.0, abs=1e-9)

    def test_a_plane_is_designable_and_not_manufacturable(self):
        """Kept deliberately. `blender_bridge` can make a plane and a plane is
        a zero-thickness sheet, so the validator has to say no to something
        Apex itself produced."""
        report = forge.validate(forge.primitive("plane", {"width": 50, "depth": 50}))
        assert report.verdict == forge.NOT_MANUFACTURABLE
        assert any(f.check == "volume" and f.level == forge.FAIL
                   for f in report.findings)


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------

class TestTopologyFindsEachFaultSeparately:

    def test_a_closed_box_is_watertight(self):
        topo = forge.topology(cube_mesh())
        assert topo.watertight and topo.consistent_winding
        assert (topo.boundary_edges, topo.nonmanifold_edges, topo.flipped_edges) == (0, 0, 0)

    def test_a_missing_face_leaves_four_open_edges(self):
        topo = forge.topology(open_box())
        assert topo.boundary_edges == 4
        assert not topo.watertight

    def test_one_reversed_triangle_is_caught_by_the_edge_directions(self):
        """A single flipped face keeps the mesh closed and keeps the volume
        positive. Only the edge-direction test sees it."""
        tris = cube_mesh().tris.copy()
        tris[0] = tris[0][::-1]
        mesh = forge.Mesh(tris)
        topo = forge.topology(mesh)
        assert topo.boundary_edges == 0, "still closed — that is the point"
        assert mesh.volume_mm3() > 0, "still positive — that is also the point"
        assert topo.flipped_edges == 3
        assert forge.validate(mesh).verdict == forge.NOT_MANUFACTURABLE

    def test_a_wholly_inverted_box_has_consistent_winding_and_negative_volume(self):
        """The complement of the test above, and the reason the volume sign is
        checked at all: flip EVERY face and the edge-direction test is happy,
        because the faces still agree with each other. They just all agree on
        the wrong answer."""
        mesh = cube_mesh(flip=True)
        topo = forge.topology(mesh)
        assert topo.watertight and topo.consistent_winding
        assert mesh.volume_mm3() < 0
        report = forge.validate(mesh)
        assert report.verdict == forge.NOT_MANUFACTURABLE
        assert any(f.check == "normals" and f.level == forge.FAIL
                   for f in report.findings)

    def test_a_third_face_on_one_edge_is_non_manifold(self):
        tris = cube_mesh().tris
        extra = np.array([[[0.0, 0.0, 0.0], [20.0, 0.0, 0.0], [0.0, 0.0, -5.0]]])
        topo = forge.topology(forge.Mesh(np.concatenate([tris, extra])))
        assert topo.nonmanifold_edges >= 1
        assert not topo.watertight

    def test_a_duplicated_triangle_is_reported(self):
        tris = cube_mesh().tris
        mesh = forge.Mesh(np.concatenate([tris, tris[:1]]))
        assert forge.topology(mesh).duplicate_faces == 1

    def test_a_zero_area_triangle_is_degenerate(self):
        tris = cube_mesh().tris
        flat = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]])
        assert forge.topology(forge.Mesh(np.concatenate([tris, flat]))).degenerate_faces == 1

    def test_welding_shares_corners_that_stl_stores_separately(self):
        """STL has no topology at all — 12 loose triangles, 36 corners. Every
        question above depends on recovering the 8 that are really there."""
        verts, faces = forge.weld(cube_mesh())
        assert len(verts) == 8
        assert faces.shape == (12, 3)

    def test_a_hollow_box_is_a_solid_with_a_void(self):
        mesh = hollow_box(20.0, 2.0)
        topo = forge.topology(mesh)
        assert topo.watertight and topo.consistent_winding
        # 20^3 minus 16^3 — the difference, not the sum. If the cavity were
        # wound the same way as the shell this would read 12096.
        assert mesh.volume_mm3() == pytest.approx(8000 - 4096)


# --------------------------------------------------------------------------
# Wall thickness — the check that separates this module from a viewer
# --------------------------------------------------------------------------

class TestWallThicknessIsMeasured:

    def test_a_two_millimetre_wall_measures_two_millimetres(self):
        samples, why = forge.thickness_samples(hollow_box(20.0, 2.0))
        assert why == ""
        assert samples.min() == pytest.approx(2.0, abs=1e-6)

    def test_a_solid_block_measures_its_shortest_dimension(self):
        samples, _ = forge.thickness_samples(cube_mesh(50, 40, 30))
        assert samples.min() == pytest.approx(30.0, abs=1e-6)

    def test_a_wall_below_the_nozzle_fails_a_mesh_that_passes_everything_else(self):
        """The load-bearing test of the module. This box is watertight,
        correctly wound, positive volume, fits the plate and is the right
        scale. Every check but one says yes, and it cannot be printed."""
        mesh = hollow_box(20.0, 0.3)
        topo = forge.topology(mesh)
        assert topo.watertight and topo.consistent_winding and mesh.volume_mm3() > 0

        report = forge.validate(mesh, nozzle_mm=0.4)
        assert report.verdict == forge.NOT_MANUFACTURABLE
        failures = [f.check for f in report.failures()]
        assert failures == ["wall thickness"], (
            f"only the thickness check should object, got {failures}")

    def test_the_same_wall_passes_a_finer_nozzle(self):
        """Proves the limit is actually consulted rather than a constant. A
        0.3mm wall is impossible at 0.4mm and routine at 0.1mm."""
        mesh = hollow_box(20.0, 0.3)
        assert forge.validate(mesh, nozzle_mm=0.4).verdict == forge.NOT_MANUFACTURABLE
        assert forge.validate(mesh, nozzle_mm=0.1).verdict == forge.MANUFACTURABLE

    def test_an_explicit_minimum_overrides_the_nozzle_rule(self):
        mesh = hollow_box(20.0, 1.0)
        assert forge.validate(mesh, nozzle_mm=0.4).ok
        assert not forge.validate(mesh, nozzle_mm=0.4, min_wall_mm=1.5).ok

    def test_the_measurement_is_deterministic(self):
        """Sampling strides rather than draws at random. A verdict that
        differs between two runs of the same file cannot be trusted and cannot
        be tested."""
        mesh = forge.primitive("torus", {"diameter": 60, "tube_diameter": 20})
        a, _ = forge.thickness_samples(mesh)
        b, _ = forge.thickness_samples(mesh)
        assert np.array_equal(a, b)

    def test_a_sheet_cannot_be_measured_and_says_so(self):
        samples, why = forge.thickness_samples(forge.primitive("plane", {"width": 50, "depth": 50}))
        assert samples is None
        assert "no ray" in why

    def test_too_many_triangles_declines_rather_than_guesses(self, monkeypatch):
        monkeypatch.setattr(forge, "MAX_TRIANGLES_FOR_THICKNESS", 5)
        samples, why = forge.thickness_samples(cube_mesh())
        assert samples is None
        assert "past the 5" in why

    def test_declining_to_measure_is_never_a_pass(self, monkeypatch):
        """The whole doctrine in one assertion. A mesh that is perfect in every
        measurable way must still not come back `manufacturable` when the
        thickness check did not run."""
        monkeypatch.setattr(forge, "MAX_TRIANGLES_FOR_THICKNESS", 5)
        report = forge.validate(cube_mesh())
        assert report.failures() == []
        assert report.verdict == forge.UNVERIFIED
        assert not report.ok

    def test_skipping_the_check_is_also_never_a_pass(self):
        report = forge.validate(cube_mesh(), measure_thickness=False)
        assert report.verdict == forge.UNVERIFIED

    def test_the_report_says_measured_not_minimum(self):
        """Ray sampling can over-report thickness and never under-report it, so
        the wording has to be `thinnest measured`. A report that says
        `thinnest` is claiming something the method cannot deliver."""
        text = forge.validate(hollow_box(20.0, 2.0)).describe()
        assert "thinnest measured" in text


# --------------------------------------------------------------------------
# Overhangs
# --------------------------------------------------------------------------

class TestOverhangs:

    def test_the_face_a_part_rests_on_is_not_an_overhang(self):
        """A cube's underside is a 90-degree down-facing surface and needs no
        support at all. Flagging it would make the check noise, and noise gets
        ignored, which is the same as not having the check."""
        unsupported, _, steepest = forge.overhang_report(cube_mesh(), 45.0)
        assert unsupported == 0.0
        assert steepest == 0.0
        assert forge.validate(cube_mesh()).verdict == forge.MANUFACTURABLE

    def test_an_internal_ceiling_is_an_overhang(self):
        """The same flat down-facing square, one wall thickness above the
        plate, is a real 90-degree overhang — so the exclusion above is about
        resting on the bed, not about pointing downwards."""
        unsupported, _, steepest = forge.overhang_report(hollow_box(20.0, 2.0), 45.0)
        assert steepest == pytest.approx(90.0)
        assert unsupported == pytest.approx(16.0 * 16.0)

    @pytest.mark.parametrize("angle", [20.0, 30.0, 44.0, 46.0, 60.0, 80.0])
    def test_the_angle_is_measured_from_vertical_and_is_exact(self, angle):
        _, _, steepest = forge.overhang_report(ramp(angle), 45.0)
        assert steepest == pytest.approx(angle, abs=1e-6)

    def test_the_threshold_decides(self):
        assert forge.overhang_report(ramp(44.0), 45.0)[0] == 0.0
        assert forge.overhang_report(ramp(46.0), 45.0)[0] > 0.0
        # ...and moving the threshold moves the answer, which a hardcoded 45
        # would not.
        assert forge.overhang_report(ramp(46.0), 50.0)[0] == 0.0

    def test_an_overhang_is_a_warning_not_a_refusal(self):
        """Supports exist. A part that needs them is still a part."""
        report = forge.validate(ramp(80.0))
        assert report.verdict == forge.MANUFACTURABLE
        assert any(f.check == "overhangs" and f.level == forge.WARN
                   for f in report.findings)


# --------------------------------------------------------------------------
# Size, scale and the verdict
# --------------------------------------------------------------------------

class TestSizeAndScale:

    def test_a_part_larger_than_the_machine_is_refused(self):
        big = cube_mesh(300, 300, 300)
        report = forge.validate(big, build_mm=(256, 256, 256))
        assert report.verdict == forge.NOT_MANUFACTURABLE
        assert any(f.check == "build volume" and f.level == forge.FAIL
                   for f in report.findings)
        # The same part on a bigger machine is fine — the limit is consulted.
        assert forge.validate(big, build_mm=(400, 400, 400)).ok

    def test_a_model_left_in_metres_is_caught_by_its_size(self):
        """0.05 x 0.04 x 0.03 is a 50mm box whose unit conversion never
        happened. No format states its unit, so size is the only evidence
        there is — and the message has to name the likely cause, because
        `scale FAIL` on its own tells nobody what to do."""
        report = forge.validate(forge.Mesh(cube_mesh(0.05, 0.04, 0.03).tris))
        scale = [f for f in report.findings if f.check == "scale"][0]
        assert scale.level == forge.FAIL
        assert "metres" in scale.message and "inches" in scale.message

    def test_a_small_but_plausible_part_is_a_warning_not_a_refusal(self):
        report = forge.validate(hollow_box(4.0, 1.0))
        scale = [f for f in report.findings if f.check == "scale"][0]
        assert scale.level == forge.WARN

    def test_the_report_prints_both_units(self):
        text = forge.validate(cube_mesh(25.4, 25.4, 25.4)).describe()
        assert "mm" in text and "in)" in text and "1.000" in text


class TestTheVerdict:

    def test_unknown_outranks_ok(self):
        report = forge.Report([forge.Finding("a", forge.OK, ""),
                               forge.Finding("b", forge.UNKNOWN, "")])
        assert report.verdict == forge.UNVERIFIED

    def test_fail_outranks_unknown(self):
        report = forge.Report([forge.Finding("a", forge.UNKNOWN, ""),
                               forge.Finding("b", forge.FAIL, "")])
        assert report.verdict == forge.NOT_MANUFACTURABLE

    def test_warnings_do_not_block(self):
        report = forge.Report([forge.Finding("a", forge.OK, ""),
                               forge.Finding("b", forge.WARN, "")])
        assert report.verdict == forge.MANUFACTURABLE and report.ok

    def test_an_empty_mesh_is_a_failure_not_a_pass(self):
        report = forge.validate(forge.Mesh(np.zeros((0, 3, 3))))
        assert report.verdict == forge.NOT_MANUFACTURABLE

    def test_every_finding_reaches_the_text(self):
        report = forge.validate(hollow_box(20.0, 0.3))
        text = report.describe()
        for finding in report.findings:
            assert finding.check in text
        assert "NOT MANUFACTURABLE" in text


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------

class TestSTL:

    def test_a_written_file_reads_back_as_the_same_solid(self, tmp_path):
        mesh = forge.primitive("cube", {"width": 50, "depth": 40, "height": 30})
        forge.write_stl(mesh, tmp_path / "c.stl")
        back = forge.read_stl(tmp_path / "c.stl")
        assert back.count == mesh.count
        assert back.volume_mm3() == pytest.approx(60000.0)
        assert forge.topology(back).watertight

    def test_the_layout_is_the_binary_stl_layout(self, tmp_path):
        """80-byte header, a uint32 count, 50 bytes a triangle. Asserted
        against the bytes rather than against the round trip, because a reader
        and writer that share a wrong idea of the format round-trip perfectly
        and produce a file no slicer can open."""
        forge.write_stl(cube_mesh(), tmp_path / "c.stl")
        raw = (tmp_path / "c.stl").read_bytes()
        assert len(raw) == 84 + 12 * 50
        assert struct.unpack_from("<I", raw, 80)[0] == 12

    def test_a_lying_stored_normal_is_ignored(self, tmp_path):
        """Face normals in STL are advisory and frequently wrong. Every normal
        this module uses comes from the winding, so a file full of zero normals
        still validates correctly."""
        forge.write_stl(cube_mesh(), tmp_path / "c.stl")
        raw = bytearray((tmp_path / "c.stl").read_bytes())
        for i in range(12):
            start = 84 + i * 50
            raw[start:start + 12] = b"\0" * 12
        (tmp_path / "z.stl").write_bytes(bytes(raw))
        assert forge.validate(forge.read_stl(tmp_path / "z.stl")).ok

    def test_the_normals_written_into_the_file_are_correct(self, tmp_path):
        """The reader ignores stored normals; the WRITER still has to get them
        right, because plenty of slicers do not ignore them and an inside-out
        normal there produces an inside-out part.

        Found by reverting the writer to emit zeros and watching all 97 tests
        pass — `test_a_lying_stored_normal_is_ignored` proves the reader's
        independence and says nothing about what is written.
        """
        mesh = forge.primitive("cube", {"width": 50, "depth": 40, "height": 30})
        forge.write_stl(mesh, tmp_path / "c.stl")
        raw = (tmp_path / "c.stl").read_bytes()
        stored = np.array([struct.unpack_from("<3f", raw, 84 + i * 50)
                           for i in range(mesh.count)])
        assert stored == pytest.approx(mesh.normals(), abs=1e-6)
        # Every one is a real unit vector, not a zero placeholder.
        assert np.linalg.norm(stored, axis=1) == pytest.approx(1.0, abs=1e-6)

    def test_ascii_is_read_too(self, tmp_path):
        p = tmp_path / "a.stl"
        p.write_text("solid x\nfacet normal 0 0 1\n outer loop\n"
                     "  vertex 0 0 0\n  vertex 1 0 0\n  vertex 0 1 0\n"
                     " endloop\nendfacet\nendsolid x\n")
        assert forge.read_stl(p).count == 1

    def test_binary_is_detected_by_length_not_by_the_word_solid(self, tmp_path):
        """Plenty of binary writers put "solid" in the 80-byte header. A reader
        that sniffs the first word treats those as ASCII, finds no `vertex`
        lines, and reports an empty file."""
        forge.write_stl(cube_mesh(), tmp_path / "c.stl")
        raw = bytearray((tmp_path / "c.stl").read_bytes())
        raw[0:5] = b"solid"
        (tmp_path / "s.stl").write_bytes(bytes(raw))
        assert forge.read_stl(tmp_path / "s.stl").count == 12

    def test_a_truncated_file_is_an_error_not_a_partial_mesh(self, tmp_path):
        forge.write_stl(cube_mesh(), tmp_path / "c.stl")
        raw = (tmp_path / "c.stl").read_bytes()
        (tmp_path / "t.stl").write_bytes(raw[:len(raw) - 60])
        with pytest.raises(forge.ForgeError):
            forge.read_stl(tmp_path / "t.stl")

    def test_writing_nothing_is_refused(self, tmp_path):
        with pytest.raises(forge.ForgeError):
            forge.write_stl(forge.Mesh(np.zeros((0, 3, 3))), tmp_path / "e.stl")


class TestThreeMF:
    """3MF is the one that matters: it is the only format in this chain that
    states its own unit."""

    def test_the_package_has_the_three_parts_a_reader_needs(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            names = set(z.namelist())
        assert names == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}

    def test_the_model_declares_millimetres(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            xml = z.read("3D/3dmodel.model").decode()
        assert 'unit="millimeter"' in xml

    def test_the_relationship_points_at_the_model(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            rels = z.read("_rels/.rels").decode()
        assert "/3D/3dmodel.model" in rels
        assert "3dmanufacturing/2013/01/3dmodel" in rels

    def test_it_is_indexed_and_shares_the_validator_s_welding(self, tmp_path):
        """A cube is 36 loose corners in STL and 8 shared ones here. Using the
        same `weld` as the topology check means the exported file and the
        checked topology cannot disagree about which corners are joined."""
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            xml = z.read("3D/3dmodel.model").decode()
        assert xml.count("<vertex ") == 8
        assert xml.count("<triangle ") == 12

    def test_a_round_trip_recovers_the_solid(self, tmp_path):
        mesh = forge.primitive("cylinder", {"diameter": 40, "height": 60})
        forge.write_3mf(mesh, tmp_path / "c.3mf")
        back = forge.read_3mf(tmp_path / "c.3mf")
        assert forge.topology(back).watertight
        assert back.volume_mm3() == pytest.approx(mesh.volume_mm3(), rel=1e-5)

    def test_the_unit_survives_the_round_trip_as_a_statement(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        assert "3MF states it" in forge.read_3mf(tmp_path / "c.3mf").assumed

    def test_a_file_in_other_units_is_refused_not_silently_rescaled(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf")
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            parts = {n: z.read(n) for n in z.namelist()}
        parts["3D/3dmodel.model"] = parts["3D/3dmodel.model"].replace(
            b'unit="millimeter"', b'unit="inch"')
        with zipfile.ZipFile(tmp_path / "i.3mf", "w") as z:
            for n, b in parts.items():
                z.writestr(n, b)
        with pytest.raises(forge.ForgeError) as e:
            forge.read_3mf(tmp_path / "i.3mf")
        assert "inch" in str(e.value)

    def test_a_title_cannot_break_the_xml(self, tmp_path):
        forge.write_3mf(cube_mesh(), tmp_path / "c.3mf", title='a<b>&"c')
        with zipfile.ZipFile(tmp_path / "c.3mf") as z:
            xml = z.read("3D/3dmodel.model").decode()
        assert "a&lt;b&gt;&amp;&quot;c" in xml
        assert forge.read_3mf(tmp_path / "c.3mf").count == 12

    def test_a_non_zip_is_an_error(self, tmp_path):
        (tmp_path / "x.3mf").write_bytes(b"not a zip")
        with pytest.raises(forge.ForgeError):
            forge.read_3mf(tmp_path / "x.3mf")


class TestGLB:
    """`.glb` is what `board_create` actually produces, so a Forge that could
    not ingest Apex's own output would be a demo."""

    def test_a_gltf_box_arrives_in_millimetres_the_right_way_up(self, tmp_path):
        """The concrete claim, with the expected numbers written by hand: a box
        stored as 0.05 wide, 0.03 tall and 0.04 deep in glTF's Y-up metres is
        50 x 40 x 30 mm in Apex's Z-up millimetres. Both conversions are
        assumptions, and a test that only round-tripped would confirm neither."""
        pos, idx = GLTF_BOX_50x40x30
        (tmp_path / "b.glb").write_bytes(build_glb(pos, idx))
        mesh = forge.read_glb(tmp_path / "b.glb")
        # abs=1e-3: glTF stores positions as float32, so 0.05 is really
        # 0.050000000745, and the millimetres that come out are too. That is
        # the format, not a bug — but it is why this compares approximately.
        assert mesh.size_mm() == pytest.approx([50.0, 40.0, 30.0], abs=1e-3)
        assert forge.topology(mesh).watertight
        assert abs(mesh.volume_mm3()) == pytest.approx(60000.0, rel=1e-4)

    def test_the_assumption_is_recorded_on_the_mesh(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        (tmp_path / "b.glb").write_bytes(build_glb(pos, idx))
        assumed = forge.read_glb(tmp_path / "b.glb").assumed
        assert "metres" in assumed and "Z-up" in assumed
        assert assumed in forge.validate(forge.read_glb(tmp_path / "b.glb")).describe()

    def test_a_node_scale_is_applied(self, tmp_path):
        """A `board_create` object can carry its size in a node transform
        rather than in its vertices. Reading raw vertex data would give a part
        of the wrong size with nothing to show for it."""
        pos, idx = GLTF_BOX_50x40x30
        (tmp_path / "s.glb").write_bytes(
            build_glb(pos, idx, node={"mesh": 0, "scale": [2.0, 2.0, 2.0]}))
        assert forge.read_glb(tmp_path / "s.glb").size_mm() == \
            pytest.approx([100.0, 80.0, 60.0], abs=1e-3)

    def test_a_node_matrix_is_applied_column_major(self, tmp_path):
        """glTF matrices are column-major; numpy reads row-major. Getting this
        backwards transposes every transform, which for a pure scale is
        invisible — hence the translation in the test matrix."""
        pos, idx = GLTF_BOX_50x40x30
        matrix = [2, 0, 0, 0,  0, 2, 0, 0,  0, 0, 2, 0,  0.1, 0, 0, 1]
        (tmp_path / "m.glb").write_bytes(
            build_glb(pos, idx, node={"mesh": 0, "matrix": matrix}))
        mesh = forge.read_glb(tmp_path / "m.glb")
        lo, hi = mesh.bounds_mm()
        assert (hi - lo) == pytest.approx([100.0, 80.0, 60.0], abs=1e-3)
        assert lo[0] == pytest.approx(100.0, abs=1e-3)   # 0.1 m translation, in mm

    def test_a_parent_transform_composes_onto_its_child(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        blob = build_glb(pos, idx, node={"mesh": 0, "scale": [2.0, 2.0, 2.0]})
        gltf, binc = forge._glb_chunks(blob, "t.glb")
        gltf["nodes"] = [{"children": [1], "scale": [3.0, 3.0, 3.0]},
                         {"mesh": 0, "scale": [2.0, 2.0, 2.0]}]
        gltf["scenes"] = [{"nodes": [0]}]
        (tmp_path / "p.glb").write_bytes(_reassemble(gltf, binc))
        assert forge.read_glb(tmp_path / "p.glb").size_mm() == \
            pytest.approx([300.0, 240.0, 180.0], abs=1e-2)

    def test_interleaved_vertex_data_is_read_with_its_stride(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        (tmp_path / "i.glb").write_bytes(build_glb(pos, idx, stride=32))
        assert forge.read_glb(tmp_path / "i.glb").size_mm() == \
            pytest.approx([50.0, 40.0, 30.0], abs=1e-3)

    def test_compressed_geometry_is_refused_rather_than_misread(self, tmp_path):
        """A Draco-compressed .glb has a JSON chunk that parses and buffer
        views that read — as noise. Refusing on `extensionsRequired` is the
        difference between an error and a garbage part."""
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["extensionsRequired"] = ["KHR_draco_mesh_compression"]
        (tmp_path / "d.glb").write_bytes(_reassemble(gltf, binc))
        with pytest.raises(forge.ForgeError) as e:
            forge.read_glb(tmp_path / "d.glb")
        assert "Draco" in str(e.value)

    def test_a_sparse_accessor_is_refused(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["accessors"][0]["sparse"] = {"count": 1}
        (tmp_path / "s.glb").write_bytes(_reassemble(gltf, binc))
        with pytest.raises(forge.ForgeError) as e:
            forge.read_glb(tmp_path / "s.glb")
        assert "sparse" in str(e.value)

    def test_geometry_in_a_separate_file_is_refused(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["buffers"][0]["uri"] = "scene.bin"
        (tmp_path / "e.glb").write_bytes(_reassemble(gltf, binc))
        with pytest.raises(forge.ForgeError) as e:
            forge.read_glb(tmp_path / "e.glb")
        assert "self-contained" in str(e.value)

    def test_an_accessor_reading_past_the_buffer_is_refused(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["accessors"][0]["count"] = 10_000
        (tmp_path / "o.glb").write_bytes(_reassemble(gltf, binc))
        with pytest.raises(forge.ForgeError):
            forge.read_glb(tmp_path / "o.glb")

    def test_a_scene_graph_cycle_terminates(self, tmp_path):
        """A glTF scene is meant to be a tree. A file with a cycle is broken,
        and the reader has to say so rather than walk it forever."""
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["nodes"] = [{"children": [1]}, {"children": [0], "mesh": 0}]
        gltf["scenes"] = [{"nodes": [0]}]
        (tmp_path / "c.glb").write_bytes(_reassemble(gltf, binc))
        assert forge.read_glb(tmp_path / "c.glb").count == 12

    def test_a_file_with_no_triangles_is_an_error(self, tmp_path):
        pos, idx = GLTF_BOX_50x40x30
        gltf, binc = forge._glb_chunks(build_glb(pos, idx), "t.glb")
        gltf["meshes"][0]["primitives"][0]["mode"] = 1     # LINES
        (tmp_path / "l.glb").write_bytes(_reassemble(gltf, binc))
        with pytest.raises(forge.ForgeError) as e:
            forge.read_glb(tmp_path / "l.glb")
        assert "no triangles" in str(e.value)

    def test_something_that_is_not_a_glb_is_an_error(self, tmp_path):
        (tmp_path / "x.glb").write_bytes(b"PK\x03\x04nope")
        with pytest.raises(forge.ForgeError):
            forge.read_glb(tmp_path / "x.glb")


def _reassemble(gltf: dict, blob: bytes) -> bytes:
    js = json.dumps(gltf).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total)
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


class TestReadAny:

    def test_each_extension_goes_to_its_reader(self, tmp_path):
        mesh = cube_mesh()
        forge.write_stl(mesh, tmp_path / "a.stl")
        forge.write_3mf(mesh, tmp_path / "a.3mf")
        pos, idx = GLTF_BOX_50x40x30
        (tmp_path / "a.glb").write_bytes(build_glb(pos, idx))
        for name in ("a.stl", "a.3mf", "a.glb"):
            assert forge.read_any(tmp_path / name).count == 12

    def test_an_unknown_extension_is_refused_not_sniffed(self, tmp_path):
        """These formats are different enough that guessing produces a
        plausible mesh rather than an error."""
        (tmp_path / "a.obj").write_text("v 0 0 0\n")
        with pytest.raises(forge.ForgeError) as e:
            forge.read_any(tmp_path / "a.obj")
        assert ".obj" in str(e.value)


# --------------------------------------------------------------------------
# The gate: export validates first
# --------------------------------------------------------------------------

class TestExportRefusesWhatCannotBeMade:
    """"Reaches a validated manufacturable representation" is one step, not
    two that happen to both occur. A Forge that writes first and validates
    afterwards produces files whose verdict nobody reads."""

    def test_an_unmakeable_mesh_produces_no_file_at_all(self, tmp_path):
        out = tmp_path / "p.3mf"
        with pytest.raises(forge.ForgeError):
            forge.export(forge.primitive("plane", {"width": 50, "depth": 50}), out)
        assert not out.exists(), "a refusal that still writes the file is not a refusal"

    def test_the_refusal_says_which_check_objected(self, tmp_path):
        with pytest.raises(forge.ForgeError) as e:
            forge.export(hollow_box(20.0, 0.3), tmp_path / "t.3mf")
        assert "cannot be extruded" in str(e.value)

    def test_an_unverified_mesh_is_also_refused(self, tmp_path):
        """Not `manufacturable` and not `not_manufacturable` either. A check
        that could not run must not become a file."""
        report = forge.validate(cube_mesh(), measure_thickness=False)
        assert report.verdict == forge.UNVERIFIED
        out = tmp_path / "u.3mf"
        with pytest.raises(forge.ForgeError) as e:
            forge.export(cube_mesh(), out, report=report)
        assert "not known to be makeable" in str(e.value)
        assert not out.exists()

    def test_force_writes_and_still_returns_the_failing_report(self, tmp_path):
        out = tmp_path / "p.3mf"
        path, report = forge.export(
            forge.primitive("plane", {"width": 50, "depth": 50}), out, force=True)
        assert path.exists()
        assert report.verdict == forge.NOT_MANUFACTURABLE

    def test_a_good_mesh_exports_to_both_formats(self, tmp_path):
        mesh = forge.primitive("cube", {"width": 50, "depth": 40, "height": 30})
        for fmt, name in ((forge.THREEMF, "c.3mf"), (forge.STL, "c.stl")):
            path, report = forge.export(mesh, tmp_path / name, fmt=fmt)
            assert path.exists() and report.ok
            assert forge.read_any(path).volume_mm3() == pytest.approx(60000.0)

    def test_an_unknown_format_is_refused_before_anything_is_validated(self, tmp_path):
        with pytest.raises(forge.ForgeError) as e:
            forge.export(cube_mesh(), tmp_path / "c.obj", fmt="obj")
        assert "obj" in str(e.value)

    def test_the_report_is_not_recomputed_when_one_is_given(self, tmp_path):
        """`export` takes the report the caller already showed the user. If it
        silently revalidated, the file could be written against a different
        verdict from the one on screen."""
        sentinel = forge.Report([forge.Finding("x", forge.OK, "hand-made")])
        _, report = forge.export(cube_mesh(), tmp_path / "c.3mf", report=sentinel)
        assert report is sentinel


# --------------------------------------------------------------------------
# Cross-verification against an implementation that is not mine
# --------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "forge"


class TestAgainstFilesApexDidNotWrite:
    """Every test above this line checks my reader against my writer. Two
    halves that share a wrong idea of a format round-trip perfectly and produce
    files no machine can open, so none of them can settle whether these files
    are really STL and really glTF.

    These fixtures were written by `trimesh`, an unrelated library, and
    committed. trimesh is NOT a dependency of Apex and is not imported here —
    what is checked in is its OUTPUT, with the answers written down. That keeps
    the cross-check permanent and offline instead of conditional on whether
    someone happens to have a library installed.
    """

    def test_a_third_party_glb_reads_at_the_right_size_and_orientation(self):
        """0.05 x 0.03 x 0.04 in glTF's own units and axes. If either the
        metres-to-millimetres scale or the Y-up-to-Z-up swap were wrong, this
        would come out as 0.05 x 0.04 x 0.03, or as 50 x 30 x 40."""
        mesh = forge.read_glb(FIXTURES / "box_50x40x30mm.glb")
        assert mesh.size_mm() == pytest.approx([50.0, 40.0, 30.0], abs=1e-3)
        assert forge.topology(mesh).watertight
        assert mesh.volume_mm3() == pytest.approx(60000.0, rel=1e-4)
        assert forge.validate(mesh).verdict == forge.MANUFACTURABLE

    def test_a_third_party_binary_stl_reads_identically(self):
        mesh = forge.read_stl(FIXTURES / "box_50x40x30mm.stl")
        assert mesh.count == 12
        assert mesh.size_mm() == pytest.approx([50.0, 40.0, 30.0], abs=1e-3)
        assert mesh.volume_mm3() == pytest.approx(60000.0, rel=1e-4)
        topo = forge.topology(mesh)
        assert topo.watertight and topo.consistent_winding


class TestAgainstTrimeshIfItIsInstalled:
    """The other direction: does an unrelated library accept what Apex WRITES.

    Skipped when trimesh is absent, which is the normal case — it is a
    verification tool, not a dependency. A skip is reported as a skip, so this
    never reads as a pass that did not happen.
    """

    @pytest.fixture(autouse=True)
    def _trimesh(self):
        self.trimesh = pytest.importorskip(
            "trimesh", reason="verification-only; not an Apex dependency")

    @pytest.mark.parametrize("shape,dims", [
        ("cube", {"width": 50, "depth": 40, "height": 30}),
        ("cylinder", {"diameter": 40, "height": 60}),
        ("sphere", {"diameter": 40}),
        ("torus", {"diameter": 60, "tube_diameter": 20}),
    ])
    def test_both_formats_open_elsewhere_with_the_same_volume(self, shape, dims, tmp_path):
        mesh = forge.primitive(shape, dims)
        forge.write_stl(mesh, tmp_path / "a.stl")
        forge.write_3mf(mesh, tmp_path / "a.3mf")
        for name in ("a.stl", "a.3mf"):
            other = self.trimesh.load(tmp_path / name, force="mesh")
            assert other.is_watertight, f"{name} is not a solid to trimesh"
            assert other.volume == pytest.approx(mesh.volume_mm3(), rel=1e-6)

    def test_an_stl_apex_writes_is_correctly_wound_to_trimesh(self, tmp_path):
        forge.write_stl(forge.primitive("cone", {"diameter": 40, "height": 60}),
                        tmp_path / "a.stl")
        assert self.trimesh.load(tmp_path / "a.stl").is_winding_consistent

    def test_the_two_libraries_agree_a_broken_mesh_is_broken(self, tmp_path):
        broken = self.trimesh.creation.box(extents=[20, 20, 20])
        broken = self.trimesh.Trimesh(vertices=broken.vertices, faces=broken.faces[:-2])
        (tmp_path / "b.stl").write_bytes(broken.export(file_type="stl"))
        assert not broken.is_watertight
        assert not forge.topology(forge.read_stl(tmp_path / "b.stl")).watertight
