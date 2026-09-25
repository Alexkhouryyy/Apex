"""The gesture recorder and its replay — how the board gets tuned on a real hand.

What would be wrong if these failed: a recording that keeps the camera
picture (it must keep joints only); frames from a get-ready countdown filed
under the pose; a file that does not load back; or a replay that says "OK"
for a gesture that misfired — the one thing it exists to catch.
"""
import gzip
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

import config
from agent import gesture_recorder as gr
from agent import handtrack
from tests.test_pinch_hysteresis import shaped_hand
from tools import replay_gestures as rg

POSES = {
    "open": dict(index_tip=(0.04, 0.17, -0.01), thumb_tip=(0.08, 0.06, -0.01)),
    "fist": dict(index_tip=(0.02, 0.05, -0.02), thumb_tip=(0.02, 0.06, -0.035), others_curled=True),
    "pinch_hold": dict(index_tip=(0.045, 0.12, -0.04), thumb_tip=(0.04, 0.115, -0.04)),
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    gr.reset()
    monkeypatch.setattr(config, "HANDTRACK_PINCH_MEASURE", "", raising=False)
    yield
    gr.reset()


def record(tmp_path, script, pose_for=None, ready_pose=None):
    """A whole recording on a fake clock, the camera 'seeing' each pose."""
    clock = {"t": 0.0}
    def sleep(s):
        clock["t"] += s
        # The "person" follows what the SCREEN says (phase and prompt), not
        # the recorder's internals — otherwise a recorder that mislabels
        # frames would be tested by a camera that mislabels them the same way.
        st = gr.status()
        take = st.get("take") if st.get("phase") == "recording" else None
        pose = POSES.get(pose_for(take) if pose_for else take) if take else ready_pose
        if pose:
            img, world = shaped_hand(**pose)
            gr.observe(NS(hand_landmarks=[img], hand_world_landmarks=[world],
                          handedness=[[NS(category_name="Right")]]), clock["t"])
    gr.start(sleep=sleep, clock=lambda: clock["t"], out_dir=tmp_path, script=script, background=False)
    return gr.status()


SCRIPT = [("open", "open", 1), ("fist", "fist", 1), ("pinch_hold", "pinch", 1)]


class TestRecording:
    def test_it_saves_every_take_with_joints_and_no_picture(self, tmp_path):
        st = record(tmp_path, SCRIPT)
        assert st["phase"] == "done" and st["missing"] == []
        with gzip.open(st["path"], "rt") as f:
            doc = json.load(f)
        assert [s["take"] for s in doc["script"]] == ["open", "fist", "pinch_hold"]
        frame = doc["frames"][0]
        assert set(frame) == {"t", "take", "hands"}
        hand = frame["hands"][0]
        assert set(hand) == {"lm", "world", "side"}, "joints and handedness only — never an image"
        assert len(hand["lm"]) == 21 and len(hand["world"]) == 21
        assert not any(k in json.dumps(doc) for k in ("jpeg", "base64", "image"))

    def test_get_ready_countdowns_are_not_recorded_as_the_pose(self, tmp_path):
        # During every countdown the hand is PINCHING. Filed under "open" or
        # "fist", those frames would make them pinch — so if any leaked in,
        # the replay would say WRONG. (A fist here proved nothing: a fist
        # never pinches, so mislabelled fist frames were invisible.)
        st = record(tmp_path, SCRIPT, ready_pose=POSES["pinch_hold"])
        rows = rg.replay(gr.load(st["path"]))
        assert {r["take"]: r["ok"] for r in rows} == {"open": True, "fist": True, "pinch_hold": True}

    def test_a_take_with_no_hand_is_named(self, tmp_path):
        st = record(tmp_path, SCRIPT, pose_for=lambda take: None if take == "fist" else take)
        assert st["missing"] == ["fist"]

    def test_nothing_is_kept_when_not_recording(self):
        img, world = shaped_hand(**POSES["open"])
        gr.observe(NS(hand_landmarks=[img], hand_world_landmarks=[world], handedness=[]), 1.0)
        assert gr._frames == []

    def test_cancel(self):
        gr.start(sleep=lambda s: None, clock=lambda: 0.0, background=True)
        assert gr.cancel()["phase"] == "idle" and gr._active_label is None

    def test_the_script_covers_the_five_moves_and_the_misfires(self):
        takes = {t for t, _p, _s in gr.SCRIPT}
        for needed in ("open", "relaxed", "fist", "side_on", "pinch_hold", "tap_1", "grab_move",
                       "two_hands", "open_palm", "swipe_up_1", "swipe_down_1", "idle"):
            assert needed in takes
        assert all(t in rg.EXPECT for t in takes), "every take must say what it should produce"


class TestReplay:
    def test_a_misfire_is_reported_as_wrong(self, tmp_path, monkeypatch):
        # The recording is fine; the tracker is broken (fists no longer
        # vetoed). The replay must say WRONG for the fist, not OK.
        st = record(tmp_path, SCRIPT)
        monkeypatch.setattr(handtrack, "is_fist", lambda lms, world=None: False)
        rows = {r["take"]: r for r in rg.replay(gr.load(st["path"]))}
        assert rows["fist"]["ok"] is False
        assert "WRONG" in rg.report(list(rows.values()))

    def test_it_suggests_a_threshold_from_the_recording(self, tmp_path):
        script = [("open", "o", 2), ("pinch_hold", "p", 2)]
        st = record(tmp_path, script)
        value, why = rg.suggest_pinch(rg.replay(gr.load(st["path"])))
        # Identical synthetic frames are thin data; either a number or a
        # reasoned refusal, never an exception.
        assert value is None or 0 < value < 1
        assert why

    def test_the_command_line(self, tmp_path, capsys):
        st = record(tmp_path, SCRIPT)
        assert rg.main([st["path"]]) == 0
        out = capsys.readouterr().out
        assert "3/3 takes as asked" in out and "Pinch threshold" in out


class TestRoute:
    @pytest.fixture
    def client(self, monkeypatch):
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "t")
        with TestClient(server.app) as c:
            c.headers["Authorization"] = "Bearer t"
            yield c

    def test_needs_tracking(self, client, monkeypatch):
        monkeypatch.setattr(handtrack, "active_tracker", lambda: None)
        r = client.post("/api/board/record", json={"action": "start"})
        assert r.status_code == 400 and "HANDTRACK_ENABLED" in r.json()["detail"]

    def test_start_and_cancel(self, client, monkeypatch):
        monkeypatch.setattr(handtrack, "active_tracker", lambda: object())
        assert client.post("/api/board/record", json={"action": "start"}).json()["phase"] == "ready"
        assert client.post("/api/board/record", json={"action": "cancel"}).json()["phase"] == "idle"
        assert client.post("/api/board/record", json={"action": "start"},
                           headers={"Origin": "https://evil.example"}).status_code == 403
