"""board_create / board_recolor through the real tool dispatcher.

test_blender_bridge.py proves the validation and protocol logic in isolation.
This file proves the OTHER half: that a successful create actually lands a
file inside the props jail and a card on the board, that a Blender-side
refusal reaches the model as readable text rather than an exception, and that
recolor finds the right object and never clobbers the previous export.

Everything here fakes agent.blender_bridge — no socket, no Blender, same
seam the pure logic tests use.
"""
import json

import pytest

from agent import core, props
from agent.board import get_board


@pytest.fixture(autouse=True)
def _clean_board():
    get_board().clear()
    core._BLENDER_OBJECTS.clear()
    yield
    get_board().clear()
    core._BLENDER_OBJECTS.clear()


class _FakeBridge:
    """Stands in for agent.blender_bridge, writing a real (tiny) file to a
    real export_dir so the copy-into-the-props-jail step is genuinely
    exercised, not mocked away."""

    def __init__(self, export_dir):
        self.export_dir = str(export_dir)
        self.calls = []

    def create_object(self, shape, dims_mm, color=None, name=""):
        self.calls.append(("create", shape, name))
        filename = f"{name.lower().replace(' ', '-')}-abc123.glb"
        (self.export_dir_path() / filename).write_bytes(b"glTF-fake-bytes")
        return {"name": name.lower().replace(" ", "-"), "slug": name,
                "filename": filename, "export_dir": self.export_dir}

    def recolor_object(self, blender_name, color):
        self.calls.append(("recolor", blender_name, color))
        filename = f"{blender_name}-recolored.glb"
        (self.export_dir_path() / filename).write_bytes(b"glTF-recolored-bytes")
        return {"name": blender_name, "slug": blender_name,
                "filename": filename, "export_dir": self.export_dir}

    def export_dir_path(self):
        from pathlib import Path
        p = Path(self.export_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


class _FailingBridge:
    class BlenderError(Exception):
        pass

    def create_object(self, *a, **k):
        raise self.BlenderError("nothing is listening on 127.0.0.1:8799")


@pytest.fixture
def jail(tmp_path, monkeypatch):
    root = tmp_path / "props"
    root.mkdir()
    monkeypatch.setattr(props, "props_root", lambda: root)
    return root


class TestBoardCreate:
    def test_a_successful_create_lands_a_file_and_a_card(self, monkeypatch, jail, tmp_path):
        from agent import blender_bridge
        fake = _FakeBridge(tmp_path / "blender_exports")
        monkeypatch.setattr(blender_bridge, "create_object", fake.create_object)
        monkeypatch.setattr(blender_bridge, "BlenderError", Exception)

        out = core._execute_tool("board_create", {
            "shape": "cube", "dims_mm": {"width": 50, "depth": 50, "height": 50},
            "title": "Test Object"})

        assert "created" in out and "board" in out
        assert (jail / "created").is_dir()
        files = list((jail / "created").glob("*.glb"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"glTF-fake-bytes"

        cards = get_board().cards()
        assert len(cards) == 1
        assert cards[0]["title"] == "Test Object" and cards[0]["kind"] == "model"

    def test_the_object_name_is_remembered_for_recolor(self, monkeypatch, jail, tmp_path):
        from agent import blender_bridge
        fake = _FakeBridge(tmp_path / "blender_exports")
        monkeypatch.setattr(blender_bridge, "create_object", fake.create_object)
        monkeypatch.setattr(blender_bridge, "BlenderError", Exception)

        core._execute_tool("board_create", {
            "shape": "sphere", "dims_mm": {"diameter": 30}, "title": "My Ball"})
        assert "my ball" in core._BLENDER_OBJECTS

    def test_blender_being_unreachable_is_reported_not_raised(self, monkeypatch, jail):
        from agent import blender_bridge
        monkeypatch.setattr(blender_bridge, "create_object", _FailingBridge().create_object)
        monkeypatch.setattr(blender_bridge, "BlenderError", _FailingBridge.BlenderError)

        out = core._execute_tool("board_create", {
            "shape": "cube", "dims_mm": {"width": 50, "depth": 50, "height": 50}})
        assert "[Blender]" in out and "nothing is listening" in out
        assert get_board().cards() == [], "a failed create must not add a card"


class TestBoardRecolor:
    def test_recoloring_an_unknown_title_is_refused(self):
        out = core._execute_tool("board_recolor",
                                 {"title": "Never Created", "color": "red"})
        assert "wasn't created" in out

    def test_a_successful_recolor_updates_the_cards_src_without_losing_the_old_file(
            self, monkeypatch, jail, tmp_path):
        from agent import blender_bridge
        fake = _FakeBridge(tmp_path / "blender_exports")
        monkeypatch.setattr(blender_bridge, "create_object", fake.create_object)
        monkeypatch.setattr(blender_bridge, "recolor_object", fake.recolor_object)
        monkeypatch.setattr(blender_bridge, "BlenderError", Exception)

        core._execute_tool("board_create", {
            "shape": "cube", "dims_mm": {"width": 50, "depth": 50, "height": 50},
            "title": "Cube One"})
        before = list((jail / "created").glob("*.glb"))
        assert len(before) == 1
        first_src = get_board().cards()[0]["id"]

        out = core._execute_tool("board_recolor",
                                 {"title": "Cube One", "color": "metallic_blue"})
        assert "recolored" in out

        after = list((jail / "created").glob("*.glb"))
        assert len(after) == 2, "the original export must still be on disk"
        assert {p.read_bytes() for p in after} == {b"glTF-fake-bytes", b"glTF-recolored-bytes"}

        card = [c for c in get_board().cards() if c["id"] == first_src][0]
        assert card["src"].endswith("recolored.glb"), "the card must point at the NEW export"
