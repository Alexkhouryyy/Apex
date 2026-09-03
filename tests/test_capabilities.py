"""What a node can do, established by testing rather than by being told.

Step 4 of docs/PHASE_6_7_PLAN.md. This is agent/mcp_policy.py's asymmetry one
layer out: a node reporting "I can run Blender" is a claim by the party the
claim is about, and a dispatcher that believes it queues work that can never
run — where it sits looking in-progress, which is worse than a refusal because
nothing appears wrong.

`TestNothingCanDeclareACapability` is the load-bearing class. The enforcement is
an absence — there is no setter — and an absence is exactly the kind of property
that quietly stops being true.
"""
import time

import pytest

import config
from agent import capabilities as cap
from agent import longterm


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "caps.db"))
    cap.init_db()


class TestNothingCanDeclareACapability:
    def test_there_is_no_setter_taking_a_name_and_a_value(self):
        """The enforcement is the absence of a function. Asserted by walking the
        module's own signatures, because "we just didn't write one" is not a
        property that survives a year of edits."""
        import inspect
        for name, fn in vars(cap).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            params = set(inspect.signature(fn).parameters)
            assert not ({"state"} <= params or {"available"} <= params), (
                f"cap.{name} takes a capability value from its caller. The only "
                f"way anything gets written is refresh(), which runs the probes "
                f"on the machine they describe.")

    def test_refresh_writes_only_what_the_probes_returned(self, db, monkeypatch):
        monkeypatch.setitem(cap.PROBES, "blender",
                            lambda: cap.Probe(cap.NO, "not installed"))
        cap.refresh("laptop")
        assert cap.of("laptop")["blender"]["state"] == cap.NO
        assert cap.can("laptop", "blender") is False

    def test_a_capability_never_probed_is_not_usable(self, db):
        """A node cannot acquire an ability by having not been asked about it."""
        cap.refresh("laptop")
        assert cap.can("laptop", "3d_printer") is False

    def test_an_unknown_device_can_do_nothing(self, db):
        assert cap.can("a-machine-nobody-has-seen", "shell") is False


class TestTheThreeStates:
    def test_unknown_is_not_usable_but_is_not_no(self, db, monkeypatch):
        """For dispatch they behave the same. For a person reading the
        dashboard, "Blender is not installed" and "the Blender probe crashed"
        are different problems, and one boolean would make them one sentence."""
        monkeypatch.setitem(cap.PROBES, "blender",
                            lambda: cap.Probe(cap.UNKNOWN, "socket module gone"))
        cap.refresh("laptop")
        row = cap.of("laptop")["blender"]
        assert row["state"] == cap.UNKNOWN
        assert row["usable"] is False
        assert row["state"] != cap.NO

    def test_a_probe_that_raises_becomes_unknown_not_yes(self, db, monkeypatch):
        def boom():
            raise RuntimeError("exploded")
        monkeypatch.setitem(cap.PROBES, "blender", boom)
        assert cap.probe_all()["blender"].state == cap.UNKNOWN

    def test_one_broken_probe_does_not_wipe_the_others(self, db, monkeypatch):
        """A sweep that aborts leaves a node with no capabilities recorded at
        all, which reads identically to a node that can do nothing."""
        def boom():
            raise RuntimeError("exploded")
        monkeypatch.setitem(cap.PROBES, "blender", boom)
        monkeypatch.setitem(cap.PROBES, "shell",
                            lambda: cap.Probe(cap.YES, "fine"))
        out = cap.probe_all()
        assert out["shell"].state == cap.YES
        assert set(out) == set(cap.PROBES)


class TestStaleness:
    def test_a_fresh_probe_is_usable(self, db, monkeypatch):
        monkeypatch.setitem(cap.PROBES, "blender", lambda: cap.Probe(cap.YES, "ok"))
        cap.refresh("laptop")
        assert cap.can("laptop", "blender") is True

    def test_an_old_probe_is_not(self, db, monkeypatch):
        """A laptop that had Blender in March and does not in September must not
        be handed a modelling task on a six-month-old probe."""
        monkeypatch.setitem(cap.PROBES, "blender", lambda: cap.Probe(cap.YES, "ok"))
        cap.refresh("laptop")
        later = time.time() + cap.MAX_AGE_SECONDS + 60
        assert cap.can("laptop", "blender", now=later) is False
        assert cap.of("laptop", now=later)["blender"]["stale"] is True

    def test_stale_is_reported_as_stale_not_as_absent(self, db, monkeypatch):
        """Dropping it from the list would make a node that vanished look like
        one that was never there."""
        monkeypatch.setitem(cap.PROBES, "blender", lambda: cap.Probe(cap.YES, "ok"))
        cap.refresh("laptop")
        later = time.time() + cap.MAX_AGE_SECONDS + 60
        rows = cap.summary(now=later)
        assert rows[0]["device_id"] == "laptop"
        assert rows[0]["capabilities"]["blender"]["state"] == cap.YES
        assert rows[0]["capabilities"]["blender"]["usable"] is False

    def test_reprobing_refreshes_the_clock(self, db, monkeypatch):
        monkeypatch.setitem(cap.PROBES, "blender", lambda: cap.Probe(cap.YES, "ok"))
        cap.refresh("laptop")
        first = cap.of("laptop")["blender"]["verified_at"]
        time.sleep(0.01)
        cap.refresh("laptop")
        assert cap.of("laptop")["blender"]["verified_at"] > first


class TestTheCameraProbeDoesNotStealTheCamera:
    """The obvious probe is `cv2.VideoCapture(0)`, and it would be a real bug:
    the webcam is exclusive, so a capability check on a timer would take the
    device away from agent/handtrack.py and each would intermittently find it
    busy. A probe with a side effect is not a probe."""

    def test_it_never_opens_a_video_device(self, monkeypatch):
        import agent.handtrack  # noqa: F401  (ensure the module is importable)

        def _must_not_run(*a, **k):
            raise AssertionError(
                "the camera probe opened the device — that takes it away from "
                "hand tracking, and the two will fight over it forever")
        monkeypatch.setattr("cv2.VideoCapture", _must_not_run)
        cap.PROBES["camera"]()          # must not raise

    def test_software_being_installed_is_not_enough(self, monkeypatch):
        """The bug this test was written for. The first version returned YES
        from `handtrack.available()` alone and said YES on a container with
        mediapipe installed and no camera at all — the exact answer this module
        exists to prevent."""
        from agent import handtrack
        monkeypatch.setattr(handtrack, "active_tracker", lambda: None)
        monkeypatch.setattr(handtrack, "available", lambda: (True, ""))
        monkeypatch.setattr("glob.glob", lambda pat: [])
        monkeypatch.setattr("sys.platform", "linux")
        p = cap.PROBES["camera"]()
        assert p.state == cap.NO, (
            "usable software with no device present must not read as a usable "
            "camera")
        assert "no video device" in p.detail

    def test_a_present_device_node_is_enough_on_linux(self, monkeypatch):
        from agent import handtrack
        monkeypatch.setattr(handtrack, "active_tracker", lambda: None)
        monkeypatch.setattr(handtrack, "available", lambda: (True, ""))
        monkeypatch.setattr("glob.glob", lambda pat: ["/dev/video0"])
        monkeypatch.setattr("sys.platform", "linux")
        assert cap.PROBES["camera"]().state == cap.YES

    def test_a_running_tracker_holding_the_camera_is_proof(self, monkeypatch):
        from agent import handtrack

        class _Fake:
            _cap = object()
            _reported = "camera_open"
        monkeypatch.setattr(handtrack, "active_tracker", lambda: _Fake())
        assert cap.PROBES["camera"]().state == cap.YES

    def test_a_tracker_that_could_not_open_it_is_a_no(self, monkeypatch):
        from agent import handtrack

        class _Fake:
            _cap = None
            _reported = "camera_busy"
        monkeypatch.setattr(handtrack, "active_tracker", lambda: _Fake())
        p = cap.PROBES["camera"]()
        assert p.state == cap.NO and "would not open" in p.detail

    def test_it_says_unknown_rather_than_guessing_off_linux(self, monkeypatch):
        """Nothing has tried the device and there is no cheap way to look, so
        nobody knows. `unknown` exists as a separate state so this case does not
        have to lie in either direction."""
        from agent import handtrack
        monkeypatch.setattr(handtrack, "active_tracker", lambda: None)
        monkeypatch.setattr(handtrack, "available", lambda: (True, ""))
        monkeypatch.setattr("sys.platform", "win32")
        assert cap.PROBES["camera"]().state == cap.UNKNOWN


class TestBlenderIsPingedNotAssumed:
    def test_the_flag_alone_is_not_evidence(self, monkeypatch):
        """BLENDER_ENABLED says someone intended it, not that Blender is
        running."""
        monkeypatch.setattr(config, "BLENDER_ENABLED", True, raising=False)
        from agent import blender_bridge
        monkeypatch.setattr(blender_bridge, "available", lambda: False)
        p = cap.PROBES["blender"]()
        assert p.state == cap.NO and "not running" in p.detail

    def test_a_ping_that_answers_is(self, monkeypatch):
        monkeypatch.setattr(config, "BLENDER_ENABLED", True, raising=False)
        from agent import blender_bridge
        monkeypatch.setattr(blender_bridge, "available", lambda: True)
        assert cap.PROBES["blender"]().state == cap.YES

    def test_disabled_is_a_no_without_touching_the_socket(self, monkeypatch):
        monkeypatch.setattr(config, "BLENDER_ENABLED", False, raising=False)
        from agent import blender_bridge

        def _must_not_run(*a, **k):
            raise AssertionError("pinged Blender while it was disabled")
        monkeypatch.setattr(blender_bridge, "available", _must_not_run)
        assert cap.PROBES["blender"]().state == cap.NO


class TestLocalModelNeedsAModelNotJustAPort:
    def test_a_server_with_nothing_pulled_is_not_usable(self, monkeypatch):
        """An Ollama with no models can serve no request, and reporting it
        available is how a delegated job fails at the far end instead of never
        being sent."""
        import io
        monkeypatch.setattr(config, "OLLAMA_BASE_URL",
                            "http://localhost:11434/v1", raising=False)
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: _ctx(b'{"models": []}'))
        p = cap.PROBES["local_model"]()
        assert p.state == cap.NO and "no models pulled" in p.detail

    def test_a_server_with_models_is(self, monkeypatch):
        monkeypatch.setattr(config, "OLLAMA_BASE_URL",
                            "http://localhost:11434/v1", raising=False)
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: _ctx(b'{"models": [{"name": "llama3"}]}'))
        assert cap.PROBES["local_model"]().state == cap.YES

    def test_nothing_listening_is_a_no(self, monkeypatch):
        monkeypatch.setattr(config, "OLLAMA_BASE_URL",
                            "http://127.0.0.1:1/v1", raising=False)
        assert cap.PROBES["local_model"]().state == cap.NO


class _ctx:
    """Minimal stand-in for urlopen's context manager."""
    def __init__(self, payload):
        self._p = payload
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._p


class TestRealProbesOnThisMachine:
    """Runs the actual probes with nothing patched. Not a strong assertion —
    what is true here is not true on the laptop — but it catches a probe that
    raises, hangs, or returns something that is not a Probe, which no amount of
    monkeypatched testing would."""

    def test_every_probe_returns_a_usable_result(self):
        out = cap.probe_all()
        assert set(out) == set(cap.PROBES)
        for name, p in out.items():
            assert isinstance(p, cap.Probe), f"{name} did not return a Probe"
            assert p.state in (cap.YES, cap.NO, cap.UNKNOWN), f"{name}: {p.state}"
            assert p.detail, f"{name} gave no reason, which is the one thing a " \
                             f"capability result must always do"
