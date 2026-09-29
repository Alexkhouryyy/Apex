"""Create with voice and hands: new shapes (gear, tube, wedge), precise voice
edits by part name / number / "this" (each a new, undoable version that keeps
untouched parts in place), and sending a build to the study library."""
import json

import numpy as np
import pytest

from agent import assembly, build3d, build_edit, core, props
from agent.blender_bridge import resolve_color

RED = [round(c, 4) for c in resolve_color("red")]
from agent.board import get_board

ENGINE = [
    {"shape": "gear", "size": [10, 2, 10], "at": [0, 1, 0], "teeth": 20, "color": "grey", "metal": True, "name": "drive gear"},
    {"shape": "gear", "size": [6, 2, 6], "at": [8, 1, 0], "teeth": 12, "color": "grey", "metal": True, "name": "idler gear"},
    {"shape": "cylinder", "size": [1.5, 12, 1.5], "at": [0, 6, 0], "color": "white", "name": "shaft"},
    {"shape": "tube", "size": [3, 3, 3], "at": [0, 1.5, 0], "hole": 0.5, "color": "black", "name": "bushing"},
    {"shape": "wedge", "size": [20, 4, 10], "at": [4, -2, 0], "color": "#334455", "name": "base ramp"},
]


@pytest.fixture
def jail(tmp_path, monkeypatch):
    root = tmp_path / "props"
    root.mkdir()
    monkeypatch.setattr(props, "props_root", lambda: root)
    monkeypatch.setenv("APEX_STUDY_DIR", str(tmp_path / "study"))
    assembly._SESSIONS.clear()
    get_board().clear()
    yield root
    get_board().clear()


class TestShapes:
    def test_gear_tube_and_wedge_validate_with_their_options(self):
        parts = build3d.validate(ENGINE)
        assert parts[0]["teeth"] == 20 and parts[0]["hole"] == 0.25
        assert parts[3]["hole"] == 0.5 and "teeth" not in parts[3] and "hole" not in parts[4]

    @pytest.mark.parametrize("bad, says", [
        ({"shape": "gear", "size": 5, "teeth": 3}, "teeth"), ({"shape": "gear", "size": 5, "teeth": 12.5}, "teeth"),
        ({"shape": "tube", "size": 5, "hole": 0.95}, "hole"), ({"shape": "tube", "size": 5, "hole": 0}, "hole"),
    ])
    def test_bad_options_are_refused_with_the_reason(self, bad, says):
        with pytest.raises(build3d.BuildError, match=says):
            build3d.validate([bad])

    def test_a_gear_has_real_teeth(self):
        p, _n, _i = build3d.unit_mesh("gear", {"teeth": 16})
        r = np.hypot(p[:, 0], p[:, 2])
        assert r.max() > 0.49 and np.quantile(r[r > 0.2], 0.1) < 0.45, "tips and roots"
        top = p[np.isclose(p[:, 1], 0.5)]
        angles = np.round(np.degrees(np.arctan2(top[:, 2], top[:, 0])) % 360, 1)
        assert len(set(angles[np.hypot(top[:, 0], top[:, 2]) > 0.48])) >= 2 * 16, "two tip corners per tooth"


class TestEdits:
    parts = build3d.validate(ENGINE)

    def test_which_part_by_name_number_this_and_all(self):
        assert build_edit.resolve(self.parts, "shaft") == [2]
        assert build_edit.resolve(self.parts, 2) == [1] and build_edit.resolve(self.parts, "part 3") == [2]
        assert build_edit.resolve(self.parts, "this", referent=4) == [4]
        assert build_edit.resolve(self.parts, "all") == [0, 1, 2, 3, 4]
        assert build_edit.resolve(self.parts, "gears", every=True) == [0, 1]
        with pytest.raises(build_edit.EditError, match="matches 2 parts"):
            build_edit.resolve(self.parts, "gear")
        with pytest.raises(build_edit.EditError, match="Nothing is selected"):
            build_edit.resolve(self.parts, "this")
        with pytest.raises(build_edit.EditError, match="No part called 'wing'.*shaft"):
            build_edit.resolve(self.parts, "wing")
        with pytest.raises(build_edit.EditError, match="no part 9"):
            build_edit.resolve(self.parts, 9)

    def test_each_operation(self):
        e = build_edit.apply
        moved, what = e(self.parts, [2], "move", {"direction": "up", "amount": 3})
        assert moved[2]["at"] == [0, 9, 0] and "moved shaft" in what
        assert e(self.parts, [2], "move", {"by": [1, 0, -2]})[0][2]["at"] == [1, 6, -2]
        assert e(self.parts, [0], "resize", {"size": 12})[0][0]["size"] == [12, 12, 12]
        assert e(self.parts, [0], "scale", {"factor": 2})[0][0]["size"] == [20, 4, 20]
        assert e(self.parts, [2], "rotate", {"by": [0, 0, 90]})[0][2]["rotate"] == [0, 0, 90]
        assert e(self.parts, [2], "color", {"color": "red"})[0][2]["color"] == RED
        assert e(self.parts, [2], "metal", {"metal": True})[0][2]["metal"] is True
        assert e(self.parts, [2], "rename", {"name": "main shaft"})[0][2]["name"] == "main shaft"
        assert len(e(self.parts, [1], "remove", {})[0]) == 4
        dup, _ = e(self.parts, [1], "duplicate", {})
        assert len(dup) == 6 and dup[5]["name"] == "idler gear copy" and dup[5]["at"][0] == 8 + 6 + 2 and dup[5]["teeth"] == 12
        added, _ = e(self.parts, [], "add", {"part": {"shape": "sphere", "size": 2, "at": [0, 13, 0], "name": "knob"}})
        assert added[-1]["name"] == "knob"
        shaped, _ = e(self.parts, [2], "shape", {"shape": "tube", "hole": 0.3})
        assert shaped[2]["shape"] == "tube" and shaped[2]["hole"] == 0.3

    def test_scaling_the_whole_build_keeps_it_together_on_its_floor(self):
        out, what = build_edit.apply(self.parts, list(range(5)), "scale", {"factor": 2})
        floor = lambda ps: min(p["at"][1] - p["size"][1] / 2 for p in ps)
        assert floor(out) == pytest.approx(floor(self.parts)) and "whole build" in what
        gap = lambda ps: ps[1]["at"][0] - ps[0]["at"][0]
        assert gap(out) == pytest.approx(2 * gap(self.parts)), "parts stay in proportion, not piled up"

    def test_refusals_say_why(self):
        with pytest.raises(build_edit.EditError, match="every part"):
            build_edit.apply(self.parts, list(range(5)), "remove", {})
        with pytest.raises(build_edit.EditError, match="colour"):
            build_edit.apply(self.parts, [0], "color", {"color": "sparkly"})
        with pytest.raises(build_edit.EditError, match="teeth"):
            build_edit.apply(self.parts, [0], "shape", {"shape": "gear", "teeth": 200})
        with pytest.raises(build_edit.EditError, match="Unknown edit"):
            build_edit.apply(self.parts, [0], "explode", {})


class TestByVoice:
    def build(self):
        out = core._execute_tool("board_build", {"title": "Gearbox", "parts": ENGINE})
        assert "built from 5 parts" in out, out
        return get_board().cards()[0]

    def test_edit_by_name_makes_an_undoable_new_version(self, jail):
        card = self.build()
        out = core._execute_tool("board_edit", {"title": "Gearbox", "part": "shaft", "op": "color", "color": "red"})
        assert "painted shaft red" in out and "v2" in out and "v1 kept" in out, out
        after = get_board().cards()[0]
        assert after["id"] == card["id"] and after["src"].endswith("v2.glb")
        assert build3d.recipe("Gearbox")[2]["color"] == RED
        get_board().undo()
        assert get_board().cards()[0]["src"].endswith("v1.glb"), "undo shows the previous version"

    def test_this_means_the_part_you_tapped_or_selected(self, jail):
        card = self.build()
        board = get_board()
        out = core._execute_tool("board_edit", {"part": "this", "op": "remove"})
        assert "Which build" in out or "Nothing is selected" in out
        board.select(card["id"])
        assert "Nothing is selected" in core._execute_tool("board_edit", {"part": "this", "op": "remove"})
        with board._lock:
            import time
            board._pointed_part = (card["id"], 1, time.time())
        out = core._execute_tool("board_edit", {"part": "this", "op": "scale", "factor": 1.5})
        assert "scaled idler gear" in out, out
        board.select(card["id"], part={"src": board.cards()[0]["src"], "kind": "part", "mesh": 2, "face": 0, "name": "shaft"})
        out = core._execute_tool("board_edit", {"part": "this", "op": "move", "direction": "up", "amount": 2})
        assert "moved shaft" in out, out

    def test_a_bad_edit_changes_nothing(self, jail):
        self.build()
        out = core._execute_tool("board_edit", {"title": "Gearbox", "part": "gear", "op": "color", "color": "red"})
        assert out.startswith("Not changed") and "matches 2 parts" in out
        assert get_board().cards()[0]["src"].endswith("v1.glb")

    def test_study_it_opens_the_build_with_its_part_names_and_notes(self, jail):
        self.build()
        events = []
        board = get_board()
        orig = board.emit
        board.emit = lambda kind, **d: (events.append((kind, d)), orig(kind, **d))[1]
        try:
            out = core._execute_tool("board_study", {"title": "Gearbox",
                                                    "notes": {"shaft": "Carries the drive gear's turning force.", "nope": "x"}})
        finally:
            board.emit = orig
        assert "study library (5 parts)" in out, out
        lib = [m for m in assembly.library() if m["category"] == "Built by you"]
        assert len(lib) == 1 and lib[0]["title"] == "Gearbox"
        model = assembly.model(lib[0]["id"])
        assert [p["name"] for p in model["parts"]] == ["drive gear", "idler gear", "shaft", "bushing", "base ramp"]
        shaft = next(p for p in model["parts"] if p["name"] == "shaft")
        assert shaft["purpose"].startswith("Carries") and shaft.get("notes_by") == "ai"
        assert any(k == "study_open" for k, _ in events)

    def test_celine_can_edit_and_study_in_discuss_mode(self):
        from agent import companion
        assert {"board_edit", "board_study"} <= companion.DISCUSS_TOOLS
        names = {t["name"] for t in core.TOOLS}
        assert {"board_edit", "board_study"} <= names
        build = next(t for t in core.TOOLS if t["name"] == "board_build")
        assert "gear" in json.dumps(build["input_schema"])
