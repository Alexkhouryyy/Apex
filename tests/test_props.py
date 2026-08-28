"""The props jail.

The board serves these files to a browser over HTTP, so "which file?" is a
security question. A path arriving from a model, a tool call or a URL must never
walk out of the props folder and return `.env`, the SQLite brain, or an SSH key.

These attack the containment rules directly rather than checking that a happy
path works. A jail whose only exercise is "it seemed fine in the browser" is not
a jail — the whole point is what it refuses.
"""
import os

import pytest

from agent import props


@pytest.fixture
def jail(tmp_path):
    """A props root with one real model, plus two things next door to steal.

    `outside.glb` matters more than `secret.env`. A traversal test aimed at a
    file with a disallowed extension is caught by the EXTENSION check and proves
    nothing about containment — verified by deleting the containment check and
    watching every such test still pass. Only a target with a legitimate
    extension can tell the two rules apart.
    """
    root = tmp_path / "props"
    (root / "models").mkdir(parents=True)
    (root / "models" / "engine.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "secret.env").write_text("ANTHROPIC_API_KEY=sk-real")
    (tmp_path / "outside.glb").write_bytes(b"glTF-not-yours")
    return root


class TestResolve:
    def test_a_real_prop_resolves(self, jail):
        assert props.resolve("models/engine.glb", jail) is not None
        assert props.resolve("logo.png", jail) is not None

    def test_backslashes_are_accepted_because_windows(self, jail):
        """Apex runs on Windows. A path typed with backslashes is a path, not an
        attack, and refusing it would look like the file was missing."""
        assert props.resolve("models\\engine.glb", jail) is not None

    @pytest.mark.parametrize("attack", [
        "../outside.glb",
        "models/../../outside.glb",
        "./../outside.glb",
        "..\\outside.glb",
        "models/./../../outside.glb",
        "models/../../../" + "../" * 8 + "outside.glb",
    ])
    def test_traversal_to_an_ALLOWED_extension_is_refused(self, jail, attack):
        """THE containment test, and the only one that isolates it.

        Every target here ends in .glb, so the extension check waves it through
        and the ONLY thing that can refuse it is containment. An earlier version
        of this aimed at `../secret.env` and `/etc/passwd`; those pass whether or
        not containment exists, because the extension rule catches them first.
        Deleting the containment check left all of them green.
        """
        assert props.resolve(attack, jail) is None

    @pytest.mark.parametrize("attack", [
        "../secret.env", "../../etc/passwd", "models/../../secret.env",
    ])
    def test_traversal_to_a_disallowed_extension_is_also_refused(self, jail, attack):
        """Belt and braces — two rules, either sufficient."""
        assert props.resolve(attack, jail) is None

    @pytest.mark.parametrize("attack", [
        "/etc/passwd", "C:/Windows/win.ini", "C:\\Windows\\win.ini",
        "//server/share/file.glb", "\\\\server\\share\\file.glb",
    ])
    def test_absolute_and_drive_paths_are_refused(self, jail, attack):
        """On Windows, joining a drive-qualified path discards the root
        entirely — `Path('/jail') / 'C:/win.ini'` is just `C:/win.ini`."""
        assert props.resolve(attack, jail) is None

    def test_a_symlink_out_of_the_jail_is_refused(self, jail, tmp_path):
        """A link planted inside the folder must not be a tunnel out of it.
        This is why resolve() follows symlinks before comparing."""
        try:
            (jail / "escape.glb").symlink_to(tmp_path / "secret.env")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")
        assert props.resolve("escape.glb", jail) is None

    def test_a_disallowed_extension_is_refused_even_inside_the_jail(self, jail):
        """Containment is not enough on its own. Every allowed extension is a
        decision to hand a file to a browser parser."""
        (jail / "notes.txt").write_text("hello")
        (jail / "script.js").write_text("alert(1)")
        assert props.resolve("notes.txt", jail) is None
        assert props.resolve("script.js", jail) is None

    def test_a_missing_file_resolves_to_nothing(self, jail):
        assert props.resolve("models/ghost.glb", jail) is None

    def test_a_directory_is_not_a_prop(self, jail):
        assert props.resolve("models", jail) is None

    @pytest.mark.parametrize("junk", ["", "   ", "/", None, 42, [], "///"])
    def test_garbage_does_not_raise(self, jail, junk):
        """This is reached from an HTTP route; a raise would be a 500 where a
        404 belongs."""
        assert props.resolve(junk, jail) is None


class TestListing:
    def test_props_are_found_at_every_depth(self, jail):
        (jail / "deep" / "deeper").mkdir(parents=True)
        (jail / "deep" / "deeper" / "car.glb").write_bytes(b"glTF")
        found = props.listing(jail)
        assert "models/engine.glb" in found
        assert "deep/deeper/car.glb" in found, "nested props must not be lost"

    def test_disallowed_files_are_not_listed(self, jail):
        (jail / "secrets.txt").write_text("no")
        assert "secrets.txt" not in props.listing(jail)

    def test_an_empty_folder_says_where_to_put_things(self, tmp_path):
        """"Nothing here" is useless on its own — the path and where to get
        models are the actual answer."""
        empty = tmp_path / "empty"
        empty.mkdir()
        out = props.describe(empty)
        assert str(empty) in out
        assert ".glb" in out and "Sketchfab" in out

    def test_models_and_images_are_listed_separately(self, jail):
        out = props.describe(jail)
        assert "3D models" in out and "images" in out

    def test_a_missing_folder_lists_nothing_rather_than_raising(self, tmp_path):
        assert props.listing(tmp_path / "does-not-exist") == []


class TestMediaType:
    @pytest.mark.parametrize("name,expected", [
        ("a.glb", "model/gltf-binary"),
        ("a.gltf", "model/gltf+json"),
        ("a.png", "image/png"),
        ("a.JPG", "image/jpeg"),
    ])
    def test_known_types(self, name, expected):
        assert props.media_type(name) == expected

    def test_an_unknown_type_is_not_guessed(self, name="a.bin"):
        assert props.media_type(name) == "application/octet-stream"

    def test_models_are_distinguished_from_images(self):
        assert props.is_model("x/y.glb") and props.is_model("A.GLTF")
        assert not props.is_model("x/y.png")


class TestServingAndTools:
    """The route and the tool, against a real server and the real dispatcher."""

    def _client(self, monkeypatch, jail):
        from fastapi.testclient import TestClient
        import config
        from agent import props
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(props, "props_root", lambda: jail)
        return TestClient(server.app)

    def test_a_real_prop_is_served_with_its_type(self, monkeypatch, jail):
        r = self._client(monkeypatch, jail).get("/board/prop/models/engine.glb")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("model/gltf-binary")

    def test_traversal_over_http_returns_404_not_the_file(self, monkeypatch, jail):
        """The route must not have its own, subtly different, version of the
        rule — every guard lives in resolve()."""
        c = self._client(monkeypatch, jail)
        for attack in ("../outside.glb", "..%2Foutside.glb",
                       "models/../../outside.glb", "../secret.env"):
            r = c.get("/board/prop/" + attack)
            assert r.status_code == 404, attack
            assert b"not-yours" not in r.content
            assert b"sk-real" not in r.content

    def test_a_refusal_is_404_rather_than_403(self, monkeypatch, jail):
        """403 tells an attacker the path exists but is forbidden, which is free
        reconnaissance. 404 tells them nothing."""
        r = self._client(monkeypatch, jail).get("/board/prop/../secret.env")
        assert r.status_code == 404

    def test_board_model_refuses_a_prop_that_is_not_there(self, monkeypatch, jail):
        import config
        from agent import core, props
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(props, "props_root", lambda: jail)
        out = core._execute_tool("board_model", {"src": "models/ghost.glb"})
        assert "not a prop" in out and str(jail) in out, \
            "must say what is wrong, not just that it failed"

    def test_board_model_refuses_an_escape(self, monkeypatch, jail):
        import config
        from agent import core, props
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(props, "props_root", lambda: jail)
        get_board().clear()
        core._execute_tool("board_model", {"src": "../outside.glb"})
        assert get_board().count() == 0, "an escaped path reached the board"

    def test_board_model_puts_a_real_one_up(self, monkeypatch, jail):
        import config
        from agent import core, props
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(props, "props_root", lambda: jail)
        get_board().clear()
        out = core._execute_tool("board_model", {"src": "models/engine.glb"})
        assert "on the board" in out
        card = get_board().cards()[0]
        assert card["kind"] == "model" and card["src"] == "models/engine.glb"
        get_board().clear()

    def test_an_image_is_not_offered_as_a_model(self, monkeypatch, jail):
        import config
        from agent import core, props
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(props, "props_root", lambda: jail)
        assert "image, not a 3D model" in core._execute_tool(
            "board_model", {"src": "logo.png"})
