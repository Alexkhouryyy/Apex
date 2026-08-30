"""Voice-driven 3D creation — the validation half, with no Blender in the loop.

Blender itself cannot be exercised here (this machine has neither Blender nor
a GPU) — that boundary is explicit, the same way hand-tracking's real-camera
half is explicit. What CAN be proven without Blender is everything this file
covers: the shape allowlist, the dimension bounds, colour parsing, and —
load-bearing — that a name never survives into a path or a Blender object name
without being slugified first. `_send` is monkeypatched to a fake so the
socket protocol itself is exercised too, without a real add-on listening.
"""
import pytest

from agent import blender_bridge as bb


class TestSlugify:
    """A created object's name becomes a Blender object name AND a filename
    stem. Unslugified, a name is a path — the same shape of bug this session
    already found twice (`.env` value injection, prop-path traversal)."""

    @pytest.mark.parametrize("raw,must_not_contain", [
        ("../../etc/passwd", "/"),
        ("..\\..\\windows\\system32", "\\"),
        ("test; rm -rf /", ";"),
        ("<script>alert(1)</script>", "<"),
        ("a" * 500, None),   # length, not content — checked separately below
    ])
    def test_dangerous_characters_never_survive(self, raw, must_not_contain):
        out = bb.slugify(raw)
        if must_not_contain:
            assert must_not_contain not in out
        assert "/" not in out and "\\" not in out and ".." not in out

    def test_length_is_capped(self):
        assert len(bb.slugify("a" * 500)) <= 60

    @pytest.mark.parametrize("raw", ["", "   ", "///", "..", "!!!"])
    def test_emptied_out_input_still_produces_a_usable_name(self, raw):
        out = bb.slugify(raw)
        assert out and all(c.isalnum() or c == "-" for c in out.replace("-", "a"))

    def test_a_normal_name_stays_readable(self):
        assert bb.slugify("Test Object") == "test-object"

    def test_slugify_is_deterministic_for_the_same_clean_input(self):
        assert bb.slugify("phone stand") == bb.slugify("phone stand")


class TestDimensionValidation:
    def test_an_unknown_shape_is_refused(self):
        dims, reason = bb.validate_dims("teapot", {"width": 50})
        assert dims is None and "teapot" in reason

    def test_every_declared_shape_is_actually_buildable(self):
        """SHAPES and SHAPE_DIMS must agree, or a shape passes the allowlist
        and then fails dimension validation with a confusing error."""
        assert set(bb.SHAPES) == set(bb.SHAPE_DIMS)

    def test_a_missing_dimension_names_what_is_missing(self):
        dims, reason = bb.validate_dims("cube", {"width": 50, "depth": 50})
        assert dims is None and "height" in reason

    @pytest.mark.parametrize("bad", [
        {"width": "not-a-number", "depth": 50, "height": 50},
        {"width": float("nan"), "depth": 50, "height": 50},
        {"width": float("inf"), "depth": 50, "height": 50},
        {"width": -10, "depth": 50, "height": 50},
        {"width": 0, "depth": 50, "height": 50},
        {"width": 999_999, "depth": 50, "height": 50},   # THE guard this file exists for
    ])
    def test_nonsense_dimensions_are_refused(self, bad):
        dims, reason = bb.validate_dims("cube", bad)
        assert dims is None and reason

    def test_a_dimension_at_the_edge_of_the_sane_range_is_accepted(self):
        dims, reason = bb.validate_dims("cube", {"width": 1, "depth": 4000, "height": 50})
        assert dims is not None, reason

    def test_dims_mm_that_is_not_a_dict_is_refused_not_raised(self):
        dims, reason = bb.validate_dims("cube", "50mm cube")
        assert dims is None and reason


class TestColorResolution:
    def test_named_colors_resolve(self):
        assert bb.resolve_color("metallic_blue") is not None
        assert bb.resolve_color("Metallic Blue") is not None   # case/space tolerant

    def test_hex_resolves(self):
        assert bb.resolve_color("#FF00AA") == pytest.approx(
            (1.0, 0.0, 170 / 255, 1.0), abs=1e-6)

    @pytest.mark.parametrize("bad", [
        "not-a-color", "#GGGGGG", [2, 0, 0], [-1, 0, 0], [0.5, 0.5],
        "javascript:alert(1)", None,
    ])
    def test_garbage_colors_return_none_not_raise(self, bad):
        assert bb.resolve_color(bad) is None

    def test_rgb_without_alpha_gets_full_opacity(self):
        assert bb.resolve_color([0.5, 0.5, 0.5])[3] == 1.0


class TestCreateObjectRefusesBeforeTouchingTheSocket:
    """Validation happens before `_send` is ever called — a bad request must
    never reach Blender at all, let alone create something wrong there."""

    def test_disabled_is_refused_with_no_socket_call(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", False, raising=False)
        calls = []
        monkeypatch.setattr(bb, "_send", lambda *a, **k: calls.append(1))
        with pytest.raises(bb.BlenderError):
            bb.create_object("cube", {"width": 50, "depth": 50, "height": 50})
        assert calls == []

    def test_a_bad_shape_never_reaches_send(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        calls = []
        monkeypatch.setattr(bb, "_send", lambda *a, **k: calls.append(1))
        with pytest.raises(bb.BlenderError):
            bb.create_object("teapot", {"width": 50})
        assert calls == []

    def test_a_bad_color_never_reaches_send(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        calls = []
        monkeypatch.setattr(bb, "_send", lambda *a, **k: calls.append(1))
        with pytest.raises(bb.BlenderError):
            bb.create_object("cube", {"width": 50, "depth": 50, "height": 50},
                             color="not-a-real-color")
        assert calls == []


class TestCreateObjectHappyPathAgainstAFakeAddon:
    """`_send` stands in for the socket. This exercises the two-step protocol
    (create_primitive then export_glb) and that a Blender-side refusal at
    EITHER step is surfaced, not swallowed."""

    def test_a_valid_request_creates_then_exports(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        seen = []

        def fake_send(cmd, timeout=None):
            seen.append(cmd["cmd"])
            if cmd["cmd"] == "create_primitive":
                return {"ok": True, "name": "test-object"}
            if cmd["cmd"] == "export_glb":
                return {"ok": True, "export_dir": "/tmp/exports"}
            raise AssertionError(f"unexpected command {cmd}")

        monkeypatch.setattr(bb, "_send", fake_send)
        result = bb.create_object("cube", {"width": 50, "depth": 50, "height": 50},
                                  name="Test Object")
        assert seen == ["create_primitive", "export_glb"]
        assert result["name"] == "test-object"
        assert result["filename"].startswith("test-object-")
        assert result["filename"].endswith(".glb")

    def test_a_refusal_at_create_primitive_stops_before_export(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        seen = []

        def fake_send(cmd, timeout=None):
            seen.append(cmd["cmd"])
            return {"ok": False, "error": "no active view layer"}

        monkeypatch.setattr(bb, "_send", fake_send)
        with pytest.raises(bb.BlenderError, match="no active view layer"):
            bb.create_object("cube", {"width": 50, "depth": 50, "height": 50})
        assert seen == ["create_primitive"], "export_glb ran after a failed create"

    def test_a_refusal_at_export_is_still_reported(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)

        def fake_send(cmd, timeout=None):
            if cmd["cmd"] == "create_primitive":
                return {"ok": True, "name": "test-object"}
            return {"ok": False, "error": "disk full"}

        monkeypatch.setattr(bb, "_send", fake_send)
        with pytest.raises(bb.BlenderError, match="disk full"):
            bb.create_object("cube", {"width": 50, "depth": 50, "height": 50})


class TestRecolorNeverOverwritesThePreviousExport:
    """'A failed generation never replaces the last valid asset' — the
    versioning rule this feature was specified against. Every recolor must get
    a NEW filename, not reuse the last one."""

    def test_two_recolors_produce_two_different_filenames(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)

        def fake_send(cmd, timeout=None):
            if cmd["cmd"] == "set_color":
                return {"ok": True}
            return {"ok": True, "export_dir": "/tmp/exports"}

        monkeypatch.setattr(bb, "_send", fake_send)
        r1 = bb.recolor_object("test-object", "red")
        r2 = bb.recolor_object("test-object", "blue")
        assert r1["filename"] != r2["filename"]

    def test_recolor_refuses_an_unknown_color_before_sending(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        calls = []
        monkeypatch.setattr(bb, "_send", lambda *a, **k: calls.append(1))
        with pytest.raises(bb.BlenderError):
            bb.recolor_object("test-object", "not-a-color")
        assert calls == []


class TestAvailability:
    def test_disabled_is_never_available(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", False, raising=False)
        monkeypatch.setattr(bb, "_send", lambda *a, **k: {"ok": True})
        assert bb.available() is False

    def test_a_connection_refusal_is_unavailable_not_an_exception(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)

        def refuse(*a, **k):
            raise bb.BlenderError("nothing is listening")
        monkeypatch.setattr(bb, "_send", refuse)
        assert bb.available() is False

    def test_enabled_and_reachable_is_available(self, monkeypatch):
        monkeypatch.setattr(bb.config, "BLENDER_ENABLED", True, raising=False)
        monkeypatch.setattr(bb, "_send", lambda *a, **k: {"ok": True})
        assert bb.available() is True
