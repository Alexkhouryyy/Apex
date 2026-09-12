"""apex_forge through the real tool dispatcher.

`tests/test_forge.py` proves the geometry. This file proves the other half —
the half that has silently failed before in this codebase: that the tool is
actually reachable, that a refusal arrives as text a model can read rather
than an exception, that a written file lands inside the props folder as a
version of the right asset, and above all that a REFUSED export leaves nothing
behind.

The last one is the phase's success check in one assertion. "Reaches a
validated manufacturable representation" means the file and the verdict cannot
come apart.
"""
from __future__ import annotations

import zipfile

import pytest

from agent import assets, core, forge, props


@pytest.fixture
def jail(tmp_path, monkeypatch):
    root = tmp_path / "props"
    root.mkdir()
    monkeypatch.setattr(props, "props_root", lambda: root)
    return root


def run(**inputs) -> str:
    return core._execute_tool("apex_forge", inputs)


class TestTheToolIsActuallyWired:

    def test_it_is_offered_to_the_model(self):
        assert any(t["name"] == "apex_forge" for t in core.TOOLS)

    def test_the_schema_requires_an_action(self):
        tool = [t for t in core.TOOLS if t["name"] == "apex_forge"][0]
        assert tool["input_schema"]["required"] == ["action"]

    def test_an_unknown_action_explains_itself(self):
        out = run(action="polish")
        assert "check" in out and "make" in out and "export" in out

    def test_an_unknown_format_is_named_as_a_format_problem(self, jail):
        """Not routed through the geometry refusal, which would tell the reader
        to fix a mesh that is perfectly fine."""
        out = run(action="make", shape="cube",
                  dims_mm={"width": 50, "depth": 40, "height": 30},
                  title="Block", format="obj")
        assert "not a format" in out
        assert "geometry" not in out
        assert assets.find_by_title("Block") is None

    def test_a_bad_shape_is_a_message_not_a_traceback(self, jail):
        out = run(action="make", shape="dodecahedron", dims_mm={"width": 10})
        assert "[Forge]" in out and "dodecahedron" in out


class TestMake:
    """`make` is Forge's own path: a measured solid, in millimetres, with no
    Blender anywhere. It is what lets this phase be proven at all."""

    def test_it_writes_a_validated_file_and_records_a_version(self, jail):
        out = run(action="make", shape="cube",
                  dims_mm={"width": 50, "depth": 40, "height": 30},
                  title="Test Block")
        assert "MANUFACTURABLE" in out
        data = assets.find_by_title("Test Block")
        assert data is not None and len(data["versions"]) == 1
        written = jail / "created" / data["id"] / data["versions"][0]["file"]
        assert written.exists() and written.suffix == ".3mf"

    def test_the_file_is_a_real_package_that_states_millimetres(self, jail):
        run(action="make", shape="cube", dims_mm={"width": 50, "depth": 40, "height": 30},
            title="Test Block")
        data = assets.find_by_title("Test Block")
        path = jail / "created" / data["id"] / data["versions"][0]["file"]
        with zipfile.ZipFile(path) as z:
            assert 'unit="millimeter"' in z.read("3D/3dmodel.model").decode()
        assert forge.read_any(path).volume_mm3() == pytest.approx(60000.0)

    def test_stl_is_available_when_asked_for(self, jail):
        run(action="make", shape="cylinder", dims_mm={"diameter": 40, "height": 60},
            title="Pin", format="stl")
        data = assets.find_by_title("Pin")
        assert data["versions"][0]["file"].endswith(".stl")

    def test_the_request_that_produced_it_is_recorded(self, jail):
        run(action="make", shape="cube", dims_mm={"width": 50, "depth": 40, "height": 30},
            title="Test Block")
        command = assets.find_by_title("Test Block")["versions"][0]["command"]
        assert command["tool"] == "apex_forge"
        assert command["dims_mm"] == {"width": 50, "depth": 40, "height": 30}

    def test_a_title_that_is_a_path_cannot_escape_the_folder(self, jail):
        """A title becomes a folder name and a filename. This codebase has
        already found this bug twice in other places."""
        run(action="make", shape="cube", dims_mm={"width": 50, "depth": 40, "height": 30},
            title="../../escape")
        assert not (jail.parent.parent / "escape").exists()
        assert list((jail / "created").iterdir())


class TestCheck:

    def test_it_reports_on_an_object_by_its_board_title(self, jail):
        run(action="make", shape="cube", dims_mm={"width": 50, "depth": 40, "height": 30},
            title="Test Block")
        out = run(action="check", title="Test Block")
        assert "MANUFACTURABLE" in out
        assert "watertight" in out and "wall thickness" in out

    def test_an_unknown_title_says_so(self, jail):
        assert "no object called" in run(action="check", title="Nothing")

    def test_a_path_outside_the_props_folder_is_refused(self, jail):
        out = run(action="check", path="../../../etc/passwd")
        assert "not a readable model" in out

    def test_a_path_inside_is_read(self, jail):
        run(action="make", shape="sphere", dims_mm={"diameter": 40}, title="Ball")
        data = assets.find_by_title("Ball")
        rel = f"created/{data['id']}/{data['versions'][0]['file']}"
        assert "MANUFACTURABLE" in run(action="check", path=rel)

    def test_it_needs_something_to_look_at(self, jail):
        assert "title or a path" in run(action="check")

    def test_the_machine_comes_from_config_not_from_a_constant(self, jail, monkeypatch):
        """A 0.3mm wall is impossible at 0.4mm and routine at 0.1mm. If the
        verdict did not move with the setting, the check would be decoration."""
        import config
        mesh = forge.Mesh(_hollow(0.3))
        monkeypatch.setattr(config, "FORGE_NOZZLE_MM", 0.4)
        assert not forge.validate(mesh, **core._forge_settings()).ok
        monkeypatch.setattr(config, "FORGE_NOZZLE_MM", 0.1)
        assert forge.validate(mesh, **core._forge_settings()).ok

    def test_the_build_volume_comes_from_config(self, jail, monkeypatch):
        import config
        mesh = forge.primitive("cube", {"width": 300, "depth": 300, "height": 300})
        monkeypatch.setattr(config, "FORGE_BUILD_X_MM", 256.0)
        monkeypatch.setattr(config, "FORGE_BUILD_Y_MM", 256.0)
        monkeypatch.setattr(config, "FORGE_BUILD_Z_MM", 256.0)
        assert not forge.validate(mesh, **core._forge_settings()).ok
        for axis in ("X", "Y", "Z"):
            monkeypatch.setattr(config, f"FORGE_BUILD_{axis}_MM", 400.0)
        assert forge.validate(mesh, **core._forge_settings()).ok


def _hollow(wall: float):
    import numpy as np
    from tests.test_forge import hollow_box
    return hollow_box(20.0, wall).tris


class TestExportRefusesAndLeavesNothingBehind:
    """The phase's success check, as assertions."""

    def test_a_refused_export_writes_no_file_and_records_no_version(self, jail):
        run(action="make", shape="plane", dims_mm={"width": 50, "depth": 50},
            title="Sheet", force=True)
        data = assets.find_by_title("Sheet")
        before = len(data["versions"])

        out = run(action="export", title="Sheet")
        assert "NOT MANUFACTURABLE" in out
        assert "Nothing was written" in out

        after = assets.find_by_title("Sheet")
        assert len(after["versions"]) == before, (
            "a refused export must not appear in the history")
        files = sorted(p.name for p in (jail / "created" / data["id"]).iterdir())
        assert files == sorted(
            ["asset.json", data["versions"][0]["file"]])

    def test_the_refusal_names_the_objection(self, jail):
        run(action="make", shape="plane", dims_mm={"width": 50, "depth": 50},
            title="Sheet", force=True)
        out = run(action="export", title="Sheet")
        assert "zero-thickness sheet" in out

    def test_make_also_refuses_and_leaves_nothing_at_all(self, jail):
        """Not even an empty asset folder. One with zero versions still reads
        as a thing Apex made when board_history lists it, which is the same
        "reported success, produced nothing" shape in a smaller costume."""
        out = run(action="make", shape="plane", dims_mm={"width": 50, "depth": 50},
                  title="Sheet")
        assert "Nothing was written" in out
        assert assets.find_by_title("Sheet") is None
        assert not (jail / "created").exists() or not list((jail / "created").iterdir())

    def test_force_writes_and_still_shows_the_verdict(self, jail):
        out = run(action="make", shape="plane", dims_mm={"width": 50, "depth": 50},
                  title="Sheet", force=True)
        assert "NOT MANUFACTURABLE" in out
        assert "despite the above" in out
        assert len(assets.find_by_title("Sheet")["versions"]) == 1

    def test_an_export_is_a_new_version_of_the_same_object(self, jail):
        """A 3MF of a design is the same object converted, not a separate thing
        with its own history — so lineage has to point back at the version it
        came from."""
        run(action="make", shape="cube", dims_mm={"width": 50, "depth": 40, "height": 30},
            title="Test Block")
        run(action="export", title="Test Block", format="stl")
        data = assets.find_by_title("Test Block")
        assert [v["file"] for v in data["versions"]] == ["v1.3mf", "v2.stl"]
        assert data["versions"][1]["parent"] == 1
        assert data["current_version"] == 2


class TestTheFabricationExtensionsAreNotServed:
    """Forge's files live in the props folder and are NOT props. A browser has
    no parser for an .stl and no business being handed one, so the serving
    allowlist stays exactly as narrow as it was."""

    def test_the_board_still_refuses_to_serve_an_stl(self, jail):
        (jail / "part.stl").write_bytes(b"x" * 200)
        assert props.resolve("part.stl") is None

    def test_forge_can_still_read_it_by_asking_for_its_own_list(self, jail):
        (jail / "part.stl").write_bytes(b"x" * 200)
        assert props.resolve(
            "part.stl", exts=props.ALLOWED_EXTS + props.FABRICATION_EXTS) is not None

    def test_the_escape_rules_are_the_same_ones(self, jail):
        """Forge passes a different extension list, not a different jail. If
        containment had been reimplemented, this is where the copies would
        start to differ."""
        for attempt in ("../outside.stl", "/etc/passwd", "C:/x.stl", "//host/x.stl"):
            assert props.resolve(
                attempt, exts=props.FABRICATION_EXTS) is None
