"""Calibrating the pinch from the board — the fix for "it grabs when I don't pinch".

The failure it fixes, as numbers: with the shipped entry of 0.70, a relaxed
hand that reads 0.55-0.65 counts as a pinch, so the board grabs things and
drags them with a hand that is just resting. Calibration measures the user's
open, RELAXED and pinched hand; relaxed must land on the not-pinch side.
"""
import random

import pytest
from fastapi.testclient import TestClient

import config
from agent import handtrack, pinch_calibration as pc


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    pc.reset()
    monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.70, raising=False)
    monkeypatch.setattr(config, "HANDTRACK_PINCH_RELEASE_RATIO", None, raising=False)
    # apply() sets the measure too: restore it, or every test after this file
    # sees a hand "calibrated in 3D" (found as order-dependent failures).
    monkeypatch.setattr(config, "HANDTRACK_PINCH_MEASURE", "", raising=False)
    yield
    pc.reset()


def spread(lo, hi, n=40, seed=1):
    r = random.Random(seed)
    return [round(r.uniform(lo, hi), 3) for _ in range(n)]


class TestCompute:
    def test_a_relaxed_hand_is_never_a_pinch_after_calibration(self):
        res = pc.compute({"open": spread(0.95, 1.3), "relaxed": spread(0.55, 0.65, seed=2),
                          "pinch": spread(0.08, 0.25, seed=3)})
        assert res["ok"], res["reason"]
        assert 0.25 < res["enter"] < 0.55, "entry must sit between the pinch and the RELAXED hand"
        assert res["enter"] < res["release"] < 0.55, "a relaxed hand must also let go"
        # The shipped default would have called every relaxed reading a pinch.
        assert all(r < 0.70 for r in spread(0.55, 0.65, seed=2))

    def test_overlapping_poses_are_refused_and_say_why(self):
        res = pc.compute({"open": spread(0.9, 1.2), "relaxed": spread(0.15, 0.3, seed=2),
                          "pinch": spread(0.1, 0.3, seed=3)})
        assert not res["ok"] and "overlap" in res["reason"]

    def test_too_few_readings_are_refused(self):
        res = pc.compute({"open": [1.0, 1.1], "relaxed": [], "pinch": [0.1]})
        assert not res["ok"] and "barely detected" in res["reason"]


class TestApply:
    def test_used_at_once_and_saved(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER=1\nHANDTRACK_PINCH_RATIO=0.70\n")
        assert pc.apply({"enter": 0.34, "release": 0.45}, env) == "saved"
        assert config.HANDTRACK_PINCH_RATIO == 0.34
        assert handtrack.pinch_release_ratio(0.34) == 0.45, "the tracker reads the new release"
        text = env.read_text()
        assert "HANDTRACK_PINCH_RATIO=0.34" in text and "HANDTRACK_PINCH_RELEASE_RATIO=0.45" in text
        assert "OTHER=1" in text and "0.70" not in text


class TestTheGuidedRun:
    def run(self, tmp_path, poses):
        """A whole calibration on a fake clock, the 'hand' holding each pose."""
        t = {"now": 0.0}
        def clock():
            return t["now"]
        def sleep(s):
            t["now"] += s
        def hands():
            st = pc.status()
            if st.get("phase") != "recording":
                return [{"ratio": 0.05}]           # changing pose: must NOT be recorded
            lo, hi = poses[st["pose"]]
            return [{"ratio": random.uniform(lo, hi)}]
        return pc.start(hands, sleep=sleep, clock=clock, env_path=tmp_path / ".env", background=False)

    def test_prompts_each_pose_and_ends_with_a_saved_result(self, tmp_path):
        seen = []
        orig = pc._set
        def spy(**kw):
            seen.append((kw.get("phase"), kw.get("pose")))
            orig(**kw)
        pc._set = spy
        try:
            self.run(tmp_path, {"open": (0.95, 1.3), "relaxed": (0.55, 0.65), "pinch": (0.1, 0.25)})
        finally:
            pc._set = orig
        order = [p for p, _ in seen]
        assert ("ready", "open") in seen and ("recording", "relaxed") in seen and ("recording", "pinch") in seen
        assert order.index("recording") > order.index("ready"), "a get-ready prompt comes first"
        st = pc.status()
        assert st["phase"] == "done" and st["ok"] and st["saved"] == "saved"
        assert 0.25 < config.HANDTRACK_PINCH_RATIO < 0.55
        assert "HANDTRACK_PINCH_RATIO" in (tmp_path / ".env").read_text()

    def test_readings_while_changing_pose_are_not_recorded(self, tmp_path):
        # The fake hand reads 0.05 (a hard pinch) during every get-ready
        # prompt. Were those recorded as "open" or "relaxed", the poses would
        # overlap and the run would refuse.
        self.run(tmp_path, {"open": (0.95, 1.3), "relaxed": (0.55, 0.65), "pinch": (0.1, 0.25)})
        assert pc.status()["ok"]

    def test_a_refusal_changes_nothing(self, tmp_path):
        self.run(tmp_path, {"open": (0.9, 1.2), "relaxed": (0.15, 0.3), "pinch": (0.1, 0.3)})
        st = pc.status()
        assert st["phase"] == "done" and not st["ok"]
        assert config.HANDTRACK_PINCH_RATIO == 0.70 and not (tmp_path / ".env").exists()

    def test_a_3d_run_is_recorded_as_calibrated_for_3d(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_MEASURE", "", raising=False)
        t = {"now": 0.0}
        def hands():
            st = pc.status()
            lo, hi = {"open": (0.95, 1.3), "relaxed": (0.55, 0.65), "pinch": (0.1, 0.25)}.get(st.get("pose"), (1, 1))
            return [{"ratio": random.uniform(lo, hi) if st.get("phase") == "recording" else 0.05, "measure": "3d"}]
        pc.start(hands, sleep=lambda s: t.__setitem__("now", t["now"] + s), clock=lambda: t["now"],
                 env_path=tmp_path / ".env", background=False)
        assert pc.status()["measure"] == "3d" and config.HANDTRACK_PINCH_MEASURE == "3d"
        assert "HANDTRACK_PINCH_MEASURE=3d" in (tmp_path / ".env").read_text()

    def test_a_run_with_any_flat_readings_is_not_called_3d(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_MEASURE", "", raising=False)
        self.run(tmp_path, {"open": (0.95, 1.3), "relaxed": (0.55, 0.65), "pinch": (0.1, 0.25)})
        assert pc.status()["measure"] == "2d"

    def test_cancel_stops_it(self):
        pc.start(lambda: [], sleep=lambda s: None, clock=lambda: 0.0, background=True)
        assert pc.cancel()["phase"] == "idle"


class TestTheRoute:
    @pytest.fixture
    def client(self, monkeypatch):
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "t")
        with TestClient(server.app) as c:
            c.headers["Authorization"] = "Bearer t"
            yield c

    def test_needs_tracking_on(self, client, monkeypatch):
        monkeypatch.setattr(handtrack, "active_tracker", lambda: None)
        r = client.post("/api/board/calibrate", json={"action": "start"})
        assert r.status_code == 400 and "HANDTRACK_ENABLED" in r.json()["detail"]

    def test_start_and_cancel(self, client, monkeypatch):
        class Tracker:
            def latest_hands(self):
                return []
        monkeypatch.setattr(handtrack, "active_tracker", lambda: Tracker())
        r = client.post("/api/board/calibrate", json={"action": "start"})
        assert r.status_code == 200 and r.json()["phase"] == "ready"
        assert client.post("/api/board/calibrate", json={"action": "cancel"}).json()["phase"] == "idle"
        assert client.post("/api/board/calibrate", json={"action": "go"}).status_code == 400
        assert client.post("/api/board/calibrate", json={"action": "start"},
                           headers={"Origin": "https://evil.example"}).status_code == 403
