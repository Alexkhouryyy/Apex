"""board_build — words to a real .glb on the board, no Blender.

What would be wrong if these failed:
- the file is not valid glTF (another program, or the board, refuses it);
- a part is inside out (the board culls its outside and draws the inside);
- sizes are off by 100x (glTF is metres; the recipe is centimetres);
- a revision overwrites the previous version instead of adding one;
- a bad recipe writes a file or a version anyway, and looks like success.

scripts/check_build3d_glb.mjs loads the same kind of file with the board's own
three.js loader and camera; this file checks the bytes and the tool.
"""
import json
import struct

import numpy as np
import pytest

from agent import build3d, core, props, companion
from agent.board import get_board

ROCKET = [
    {"shape": "cylinder", "size": [20, 60, 20], "at": [0, 30, 0], "color": "white", "name": "body"},
    {"shape": "cone", "size": [20, 25, 20], "at": [0, 72.5, 0], "color": "red", "name": "nose"},
    {"shape": "box", "size": [2, 18, 14], "at": [11, 9, 0], "color": "#ff0000", "name": "fin"},
]


@pytest.fixture
def jail(tmp_path, monkeypatch):
    root = tmp_path / "props"
    root.mkdir()
    monkeypatch.setattr(props, "props_root", lambda: root)
    get_board().clear()
    yield root
    get_board().clear()


def parse_glb(data: bytes):
    magic, version, total = struct.unpack("<4sII", data[:12])
    assert (magic, version, total) == (b"glTF", 2, len(data))
    jlen, jtype = struct.unpack("<I4s", data[12:20])
    assert jtype == b"JSON" and jlen % 4 == 0
    doc = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack("<I4s", data[20 + jlen:28 + jlen])
    assert btype == b"BIN\x00" and blen % 4 == 0
    return doc, data[28 + jlen:28 + jlen + blen]


def accessor(doc, binary, i):
    a = doc["accessors"][i]
    v = doc["bufferViews"][a["bufferView"]]
    assert v["byteOffset"] % 4 == 0 and v["byteOffset"] + v["byteLength"] <= len(binary)
    dtype = {5126: np.float32, 5125: np.uint32}[a["componentType"]]
    width = {"VEC3": 3, "SCALAR": 1}[a["type"]]
    arr = np.frombuffer(binary, dtype=dtype, count=a["count"] * width, offset=v["byteOffset"])
    return arr.reshape(-1, width) if width > 1 else arr


class TestTheFile:
    def test_it_is_valid_gltf_with_one_named_node_per_part(self):
        doc, binary = parse_glb(build3d.to_glb(build3d.validate(ROCKET), "Rocket"))
        assert doc["asset"]["version"] == "2.0"
        assert [n["name"] for n in doc["nodes"]] == ["body", "nose", "fin", "Rocket"]
        assert doc["scenes"][0]["nodes"] == [3]
        assert doc["buffers"][0]["byteLength"] == len(binary)
        for mesh in doc["meshes"]:
            prim = mesh["primitives"][0]
            pos = accessor(doc, binary, prim["attributes"]["POSITION"])
            idx = accessor(doc, binary, prim["indices"])
            a = doc["accessors"][prim["attributes"]["POSITION"]]
            # min/max are REQUIRED on POSITION by the spec, and must be exact.
            assert np.allclose(a["min"], pos.min(axis=0)) and np.allclose(a["max"], pos.max(axis=0))
            assert idx.max() < len(pos) and len(idx) % 3 == 0

    def test_sizes_are_true_metres(self):
        doc, binary = parse_glb(build3d.to_glb(build3d.validate(ROCKET[:1]), "x"))
        a = doc["accessors"][0]
        size = np.array(a["max"]) - np.array(a["min"])
        assert np.allclose(size, [0.20, 0.60, 0.20], atol=1e-4), "60 cm must be 0.6 in the file"
        assert np.isclose(a["min"][1], 0.0, atol=1e-6), "at y=30 cm, a 60 cm part stands on the floor"

    def test_colours_and_finish(self):
        doc, _ = parse_glb(build3d.to_glb(build3d.validate(
            [{"shape": "box", "size": 1, "color": "#ff0000", "metal": True}]), "x"))
        pbr = doc["materials"][0]["pbrMetallicRoughness"]
        assert pbr["baseColorFactor"] == [1.0, 0.0, 0.0, 1.0]
        assert pbr["metallicFactor"] > 0.5

    @pytest.mark.parametrize("shape", build3d.SHAPES)
    def test_every_shape_faces_outward_and_fills_its_box(self, shape):
        # Winding: each triangle's geometric normal must agree with the
        # vertex normals. Wrong winding is invisible in a screenshot of a
        # convex shape and wrong everywhere else (the torus was, until this).
        p, n, i = build3d.unit_mesh(shape)
        t = i.reshape(-1, 3)
        face = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        real = np.linalg.norm(face, axis=1) > 1e-9
        vn = n[t[:, 0]] + n[t[:, 1]] + n[t[:, 2]]
        assert (np.einsum("ij,ij->i", face[real], vn[real]) > 0).all()
        assert np.allclose(p.min(axis=0), -0.5, atol=1e-6) and np.allclose(p.max(axis=0), 0.5, atol=1e-6)

    def test_rotation_turns_a_part_and_keeps_normals_unit(self):
        flat = build3d.validate([{"shape": "box", "size": [40, 2, 10], "rotate": [0, 0, 90]}])[0]
        pos, nrm, _ = build3d.part_mesh(flat)
        size = pos.max(axis=0) - pos.min(axis=0)
        assert np.allclose(size, [0.02, 0.40, 0.10], atol=1e-5), "rotated 90 about Z, wide becomes tall"
        assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5)

    def test_squashed_sphere_normals_follow_the_surface(self):
        # Inverse-transpose: on a flattened sphere the top normal points
        # straight up, not diagonally as a naive scale would leave it.
        # Checked everywhere against the ellipsoid's true normal, the
        # gradient (x/a^2, y/b^2, z/c^2) — at the poles alone a naive scale
        # happens to agree, which is how a first version of this test passed
        # the wrong formula.
        part = build3d.validate([{"shape": "sphere", "size": [40, 4, 40]}])[0]
        pos, nrm, _ = build3d.part_mesh(part)
        semi = np.array([0.20, 0.02, 0.20])
        true = pos / semi ** 2
        true /= np.linalg.norm(true, axis=1, keepdims=True)
        assert (np.einsum("ij,ij->i", nrm, true) > 0.999).all()


class TestTheRecipe:
    @pytest.mark.parametrize("parts, says", [
        ([], "non-empty"),
        ([{"shape": "teapot", "size": 5}], "use one of"),
        ([{"shape": "box"}], "size"),
        ([{"shape": "box", "size": [1, 2]}], "3 numbers"),
        ([{"shape": "box", "size": [1, float("nan"), 1]}], "finite"),
        ([{"shape": "box", "size": [1, 99999, 1]}], "outside"),
        ([{"shape": "box", "size": 1, "at": [0, 0, 1e9]}], "outside"),
        ([{"shape": "box", "size": 1, "color": "sparkly"}], "colour"),
        ([{"shape": "box", "size": 1}] * 81, "at most"),
        (["box"], "object"),
    ])
    def test_a_bad_recipe_is_refused_with_the_reason(self, parts, says):
        with pytest.raises(build3d.BuildError, match=says):
            build3d.validate(parts)

    def test_cube_means_box_and_one_number_means_all_sides(self):
        p = build3d.validate([{"shape": "Cube", "size": 5}])[0]
        assert p["shape"] == "box" and p["size"] == [5.0, 5.0, 5.0]


class TestVersions:
    def test_a_revision_is_a_new_version_and_keeps_the_old(self, jail):
        a = build3d.build("Rocket", ROCKET)
        b = build3d.build("rocket", ROCKET[:2])            # same thing, said differently
        assert (a["version"], b["version"], b["parent"]) == (1, 2, 1)
        assert a["slug"] == b["slug"] and b["revised"] and not a["revised"]
        assert (jail / a["src"]).is_file() and (jail / b["src"]).is_file()
        assert len(build3d.recipe("ROCKET")) == 2

    def test_a_refused_recipe_writes_nothing(self, jail):
        build3d.build("Rocket", ROCKET)
        with pytest.raises(build3d.BuildError):
            build3d.build("Rocket", [{"shape": "teapot", "size": 1}])
        from agent import assets
        assert assets.find_by_title("Rocket")["current_version"] == 1
        assert sorted(p.name for p in (jail / "created" / "rocket").iterdir()) == ["asset.json", "v1.glb"]

    def test_a_different_title_never_joins_another_assets_history(self, jail):
        from agent import assets
        assets.create("rocket", "Something else", command={"tool": "board_create"})
        r = build3d.build("Rocket", ROCKET)
        assert r["slug"] != "rocket" and r["version"] == 1
        assert assets.load("rocket")["versions"] == []


class TestTheTool:
    def test_build_puts_it_on_the_board(self, jail):
        out = core._execute_tool("board_build", {"title": "Rocket", "parts": ROCKET})
        # 22 wide: the fin (x = 11 +- 1) stands 2 cm proud of the 20 cm body.
        assert "built from 3 parts" in out and "22 x 85 x 20 cm" in out, out
        cards = get_board().cards()
        assert len(cards) == 1 and cards[0]["kind"] == "model" and cards[0]["src"].endswith("v1.glb")
        assert props.is_model(cards[0]["src"]) and props.resolve(cards[0]["src"]) is not None

    def test_revising_swaps_the_card_not_adds_one(self, jail):
        core._execute_tool("board_build", {"title": "Rocket", "parts": ROCKET})
        out = core._execute_tool("board_build", {"title": "Rocket", "parts": ROCKET[:2]})
        cards = get_board().cards()
        assert len(cards) == 1 and cards[0]["src"].endswith("v2.glb")
        assert "v2" in out and "v1 is kept" in out

    def test_show_returns_the_current_parts(self, jail):
        core._execute_tool("board_build", {"title": "Rocket", "parts": ROCKET})
        out = core._execute_tool("board_build", {"title": "rocket", "action": "show"})
        assert json.loads(out.split("\n", 1)[1])[1]["name"] == "nose"
        assert "Nothing built" in core._execute_tool("board_build", {"title": "Nope", "action": "show"})

    def test_a_bad_recipe_says_why_and_adds_nothing(self, jail):
        out = core._execute_tool("board_build", {"title": "Pot", "parts": [{"shape": "teapot", "size": 3}]})
        assert out.startswith("Not built") and "teapot" in out
        assert get_board().cards() == []

    def test_it_appears_at_the_hand(self, jail, monkeypatch):
        monkeypatch.setattr(type(get_board()), "hand_anchor", lambda self: (0.2, 0.7))
        out = core._execute_tool("board_build", {"title": "Rocket", "parts": ROCKET})
        c = get_board().cards()[0]
        assert (c["x"], c["y"]) == (0.2, 0.7) and "at your hand" in out

    def test_celine_can_build_in_discuss_mode(self):
        assert "board_build" in companion.DISCUSS_TOOLS
