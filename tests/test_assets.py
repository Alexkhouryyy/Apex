"""Versioned 3D assets — an object as an artifact with a history.

The design doc's rule is that a 3D object is "a versioned engineering
artifact, not a single mesh file." Before this, board_create copied a .glb
under a random name and recorded nothing: no version, no lineage, no trace of
the request that produced it. A week later there was no way to answer "why
does this exist" or "what did it look like before the recolor".

Everything here is pure file arithmetic against a temp root — no Blender, no
board, no browser.
"""
import json

import pytest

from agent import assets


@pytest.fixture
def root(tmp_path):
    (tmp_path / "created").mkdir(parents=True)
    return tmp_path


class TestCreateAndVersion:
    def test_a_new_asset_records_the_request_that_made_it(self, root):
        cmd = {"tool": "board_create", "shape": "cube", "dims_mm": {"width": 50}}
        data = assets.create("cube-one", "Cube One", command=cmd, props_root=root)
        assert data["title"] == "Cube One"
        assert data["origin"] == cmd
        assert data["units"] == "mm"
        assert data["versions"] == [] and data["current_version"] is None

    def test_the_manifest_is_a_real_readable_file(self, root):
        """Plain JSON next to the meshes, on purpose: it travels with the
        folder, survives Apex not running, and can be read by the person whose
        design it describes."""
        assets.create("cube-one", "Cube One", command={}, props_root=root)
        p = root / "created" / "cube-one" / "asset.json"
        assert json.loads(p.read_text())["id"] == "cube-one"

    def test_versions_are_appended_never_replaced(self, root):
        """'A failed generation or export never replaces the last valid
        asset.' Every version is its own immutable record and file."""
        assets.create("s", "Stand", command={}, props_root=root)
        v1 = assets.add_version("s", "v1.glb", command={"tool": "board_create"},
                                props_root=root)
        v2 = assets.add_version("s", "v2.glb", command={"tool": "board_recolor"},
                                parent=1, props_root=root)
        data = assets.load("s", props_root=root)
        assert [v["version"] for v in data["versions"]] == [1, 2]
        assert data["current_version"] == 2
        assert v1["file"] == "v1.glb" and v2["parent"] == 1

    def test_lineage_survives(self, root):
        """'Every fabricated candidate must link back to the exact immutable
        source version' — a chain has to resolve back to its origin."""
        assets.create("s", "Stand", command={}, props_root=root)
        assets.add_version("s", "v1.glb", command={}, props_root=root)
        assets.add_version("s", "v2.glb", command={}, parent=1, props_root=root)
        assets.add_version("s", "v3.glb", command={}, parent=2, props_root=root)
        chain = {v["version"]: v["parent"] for v in
                 assets.load("s", props_root=root)["versions"]}
        assert chain == {1: None, 2: 1, 3: 2}

    def test_recreating_an_asset_keeps_its_history(self, root):
        """Reusing a name must not silently delete every prior version — that
        is precisely the data loss versioning exists to prevent."""
        assets.create("s", "Stand", command={}, props_root=root)
        assets.add_version("s", "v1.glb", command={}, props_root=root)
        assets.create("s", "Stand", command={}, props_root=root)   # again
        assert len(assets.load("s", props_root=root)["versions"]) == 1

    def test_filenames_are_numbered_from_the_manifest_not_the_folder(self, root):
        """A stray file dropped in by hand must not renumber real history.

        Both real version files are written here, not only their manifest
        entries — otherwise counting the folder and counting the manifest
        happen to agree and the test proves nothing about which one is used.
        """
        assets.create("s", "Stand", command={}, props_root=root)
        folder = root / "created" / "s"
        assets.add_version("s", "v1.glb", command={}, props_root=root)
        (folder / "v1.glb").write_bytes(b"real")
        (folder / "junk.glb").write_bytes(b"dropped in by hand")
        (folder / "screenshot.glb").write_bytes(b"also not a version")
        assert assets.next_filename("s", props_root=root) == "v2.glb", \
            "stray files renumbered the history"

    def test_add_version_to_a_nonexistent_asset_returns_none(self, root):
        assert assets.add_version("ghost", "v1.glb", command={}, props_root=root) is None


class TestLookup:
    def _stand(self, root):
        assets.create("phone-stand", "Phone Stand", command={}, props_root=root)
        assets.add_version("phone-stand", "v1.glb", command={}, props_root=root)
        assets.add_version("phone-stand", "v2.glb", command={}, parent=1,
                           props_root=root)

    def test_current_file_points_at_the_newest(self, root):
        self._stand(root)
        assert assets.current_file("phone-stand", props_root=root) == \
            "created/phone-stand/v2.glb"

    def test_an_older_version_is_still_reachable(self, root):
        """'What did it look like before?' has to be answerable."""
        self._stand(root)
        assert assets.version_file("phone-stand", 1, props_root=root) == \
            "created/phone-stand/v1.glb"

    def test_a_version_that_does_not_exist_is_none_not_a_guess(self, root):
        self._stand(root)
        assert assets.version_file("phone-stand", 99, props_root=root) is None

    @pytest.mark.parametrize("spoken", [
        "Phone Stand", "phone stand", "PHONE STAND", "  phone   stand  ",
    ])
    def test_lookup_matches_what_a_person_would_say(self, root, spoken):
        """The user says the title out loud; they will not say the slug, and
        they will not reproduce the exact spacing they used yesterday."""
        self._stand(root)
        found = assets.find_by_title(spoken, props_root=root)
        assert found is not None and found["id"] == "phone-stand"

    def test_an_unknown_title_is_none(self, root):
        self._stand(root)
        assert assets.find_by_title("Spaceship", props_root=root) is None

    def test_a_corrupt_manifest_does_not_raise(self, root):
        """A damaged file must not make the whole board unusable."""
        d = root / "created" / "broken"
        d.mkdir(parents=True)
        (d / "asset.json").write_text("{ this is not json")
        assert assets.load("broken", props_root=root) is None
        assert assets.listing(props_root=root) == []

    def test_describe_names_the_command_behind_each_version(self, root):
        assets.create("s", "Stand", command={}, props_root=root)
        assets.add_version("s", "v1.glb", props_root=root,
                           command={"tool": "board_create", "shape": "cube"})
        assets.add_version("s", "v2.glb", parent=1, props_root=root,
                           command={"tool": "board_recolor", "color": "blue"})
        text = assets.describe(assets.load("s", props_root=root))
        assert "v1" in text and "board_create" in text and "cube" in text
        assert "v2" in text and "board_recolor" in text and "blue" in text
        assert "current" in text, "it must say which version is showing"
