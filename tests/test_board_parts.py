"""Grab one part of a model: parts mode on /board.

The claims, each with the test that would catch it breaking:
- a pinch in parts mode takes the part DRAWN under the fingers, and the front
  one where parts overlap (test_the_pinch_takes_the_part_under_it);
- moving it saves the build's next version with only that part moved, in the
  direction the hand went (test_moving_the_nose_up_saves_a_version…);
- the rest of the model does not jump when the new version replaces the old
  (test_the_parts_you_did_not_touch_stay_put);
- undo brings back the old version in its old place, in one step;
- a tap asks about the part and saves nothing; an open palm cancels; holding
  still saves nothing; two hands resize;
- only builds can be taken apart, and outside parts mode a pinch still grabs
  the whole model.
scripts/check_build3d_glb.mjs separately proves that where Python thinks a
part is drawn is where three.js draws it.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from agent import board_parts, build3d, props
from agent import board as board_mod
from agent.board import ARM_DWELL_SECONDS, Board, TAP_SECONDS

ASPECT = 16 / 9
ROCKET = [
    {"shape": "cylinder", "size": [20, 60, 20], "at": [0, 30, 0], "color": "white", "name": "body"},
    {"shape": "cone", "size": [20, 25, 20], "at": [0, 72.5, 0], "color": "red", "name": "nose"},
    {"shape": "box", "size": [2, 18, 14], "at": [11, 9, 0], "color": "red", "name": "fin"},
]


@pytest.fixture
def jail(tmp_path, monkeypatch):
    root = tmp_path / "props"
    root.mkdir()
    monkeypatch.setattr(props, "props_root", lambda: root)
    return root


@pytest.fixture
def rocket(jail):
    b = Board()
    b.persist = False
    built = build3d.build("Rocket", ROCKET)
    card = b.add("model", "Rocket", src=built["src"], x=0.5, y=0.5)
    b.set_viewport(1600, 900)
    return b, card


def drawn(b, card, name):
    """Where the page draws a part's centre, as board_parts computes it."""
    _title, parts = board_parts.recipe_for_src(card.src)
    p = next(p for p in parts if p["name"] == name)
    return board_parts.project(card.as_dict(), parts, np.array([p["at"]]) / 100.0, ASPECT)[0]


def frames(b, seq, hand=0):
    for t, x, y, pinched, palm in seq:
        b.apply_hands([(x, y, pinched, palm, hand)], now=t)


def grab_and_move(b, x, y, dx, dy, t0=0.0, release_at=None):
    c = ARM_DWELL_SECONDS + 0.01
    seq = [(t0, x, y, True, False), (t0 + c, x, y, True, False)]
    for i in range(1, 6):
        seq.append((t0 + c + 0.1 * i, x + dx * i / 5, y + dy * i / 5, True, False))
    end = release_at if release_at is not None else t0 + c + 0.7
    seq.append((end, x + dx, y + dy, False, False))
    frames(b, seq)


def events(b, kind):
    return [e for e in b.events_since(0) if e["type"] == kind]


class TestPartsMode:
    def test_only_a_build_can_be_taken_apart(self, jail):
        b = Board(); b.persist = False
        prop = b.add("model", "Engine", src="models/engine.glb")
        with pytest.raises(ValueError, match="wasn't built from parts"):
            b.set_parts_mode(prop.id)
        text = b.add("card", "Note")
        with pytest.raises(ValueError):
            b.set_parts_mode(text.id)

    def test_on_and_off_are_announced_and_shown(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        assert b.cards()[0]["parts_mode"] is True
        b.set_parts_mode(None)
        assert "parts_mode" not in b.cards()[0]
        assert [e["on"] for e in events(b, "parts_mode")] == [True, False]

    def test_outside_parts_mode_a_pinch_grabs_the_whole_model(self, rocket):
        b, card = rocket
        x, y, _ = drawn(b, card, "body")
        frames(b, [(0, x, y, True, False), (ARM_DWELL_SECONDS + .01, x, y, True, False)])
        assert card.held_by == [0] and not b._part_holds


class TestGrabbingAPart:
    def test_the_pinch_takes_the_part_under_it(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        frames(b, [(0, x, y, True, False), (ARM_DWELL_SECONDS + .01, x, y, True, False)])
        assert b._part_holds[card.id]["name"] == "nose"
        assert not card.held_by, "the model itself must not move"
        assert b.cards()[0]["part"]["name"] == "nose"

    def test_the_front_part_wins_where_parts_overlap(self, rocket):
        # The fin sticks out in front of the body at its side: pinching where
        # both are drawn takes the fin, which is nearer the viewer.
        b, card = rocket
        b.set_parts_mode(card.id)
        _t, parts = board_parts.recipe_for_src(card.src)
        fin = np.array([[0.11, 0.09, 0.0]])
        x, y, _ = board_parts.project(card.as_dict(), parts, fin, ASPECT)[0]
        assert board_parts.pick(card.as_dict(), parts, x, y, ASPECT) == 2

    def test_the_front_part_wins_whatever_its_place_in_the_list(self):
        # A panel BEHIND the body, listed after it: a pinch on the middle of
        # the body is drawn over both, and must take the body in front — not
        # whichever part the recipe happens to list last.
        parts = build3d.validate([
            {"shape": "box", "size": [20, 20, 4], "at": [0, 10, 10], "name": "front"},
            {"shape": "box", "size": [40, 40, 4], "at": [0, 10, -10], "name": "back"}])
        card = {"x": .5, "y": .5, "scale": 1, "rot": 0}
        x, y, _ = board_parts.project(card, parts, np.array([[0, .1, .1]]), ASPECT)[0]
        assert board_parts.pick(card, parts, x, y, ASPECT) == 0
        flipped = [parts[1], parts[0]]
        assert board_parts.pick(card, flipped, x, y, ASPECT) == 1

    def test_a_big_models_far_part_is_still_reachable(self, rocket):
        b, card = rocket
        card.scale = 4.0                              # nose far from the model's centre
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        assert np.hypot(x - card.x, y - card.y) > board_mod.GRAB_RADIUS
        frames(b, [(0, x, y, True, False), (ARM_DWELL_SECONDS + .01, x, y, True, False)])
        assert b._part_holds.get(card.id, {}).get("name") == "nose"


class TestSaving:
    def test_moving_the_nose_up_saves_a_version_with_only_the_nose_moved(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        grab_and_move(b, x, y, 0.0, -0.06)                     # up the screen
        saved = events(b, "part_saved")
        assert len(saved) == 1 and saved[0]["part"] == "nose" and saved[0]["version"] == 2
        assert card.src.endswith("v2.glb")
        _t, parts = board_parts.recipe_for_src(card.src)
        assert parts[1]["at"][1] > 72.5 + 5, "the nose must have gone UP"
        assert parts[0]["at"] == [0.0, 30.0, 0.0] and parts[2]["at"] == [11.0, 9.0, 0.0]

    def test_the_parts_you_did_not_touch_stay_put(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        body_before, fin_before = drawn(b, card, "body"), drawn(b, card, "fin")
        x, y, _ = drawn(b, card, "nose")
        grab_and_move(b, x, y, 0.0, -0.08)
        assert card.src.endswith("v2.glb") and card.scale != 1.0, "the bounding box grew: the fit must be compensated"
        # Within a fifth of a pixel on a 1600 px board: the card's x, y and
        # scale travel to the page rounded (4 and 3 decimals), and the page
        # draws from those rounded values — exact to 1e-6 is not what is drawn.
        px = 0.2 / 1600
        assert np.allclose(drawn(b, card, "body")[:2], body_before[:2], atol=px)
        assert np.allclose(drawn(b, card, "fin")[:2], fin_before[:2], atol=px)

    def test_undo_brings_the_old_version_back_where_it_was(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        before = (card.src, card.x, card.y, card.scale)
        x, y, _ = drawn(b, card, "nose")
        grab_and_move(b, x, y, 0.03, -0.05)
        assert card.src.endswith("v2.glb")
        assert "part" in b.undo()
        assert (card.src, card.x, card.y, card.scale) == before
        b.redo()
        assert card.src.endswith("v2.glb")

    def test_two_hands_resize_the_part(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        c = ARM_DWELL_SECONDS + .01
        b.apply_hands([(x, y, True, False, 0)], now=0)
        b.apply_hands([(x, y, True, False, 0)], now=c)                        # first hand has the nose
        b.apply_hands([(x - .02, y, True, False, 0), (x + .02, y, True, False, 1)], now=c + .1)
        b.apply_hands([(x - .02, y, True, False, 0), (x + .02, y, True, False, 1)], now=c + .1 + c)   # second joins
        assert len(b._part_holds[card.id]["hands"]) == 2
        b.apply_hands([(x - .04, y, True, False, 0), (x + .04, y, True, False, 1)], now=c + .5)       # spread x2
        b.apply_hands([(x - .04, y, False, False, 0), (x + .04, y, False, False, 1)], now=c + .6)
        _t, parts = board_parts.recipe_for_src(card.src)
        assert parts[1]["size"] == pytest.approx([40, 50, 40], rel=0.02)


class TestNotSaving:
    def test_a_tap_on_a_part_asks_about_it(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        frames(b, [(0, x, y, True, False), (ARM_DWELL_SECONDS + .01, x, y, True, False),
                   (ARM_DWELL_SECONDS + .2, x, y, False, False)])
        tapped = events(b, "tapped")
        assert len(tapped) == 1 and tapped[0]["title"] == "nose of the Rocket"
        assert card.src.endswith("v1.glb") and not events(b, "part_saved")

    def test_an_open_palm_cancels(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        c = ARM_DWELL_SECONDS + .01
        frames(b, [(0, x, y, True, False), (c, x, y, True, False), (c + .3, x, y - .06, True, False),
                   (c + .4, x, y - .06, False, True)])
        assert card.src.endswith("v1.glb") and not events(b, "part_saved")
        assert "part" not in b.cards()[0], "the live preview must end"

    def test_holding_still_and_letting_go_saves_nothing(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        grab_and_move(b, x, y, 0.0, 0.0, release_at=TAP_SECONDS + 1.0)
        assert card.src.endswith("v1.glb") and not events(b, "part_saved") and not events(b, "tapped")

    def test_swipes_wait_while_a_part_is_held(self, rocket):
        b, card = rocket
        b.set_parts_mode(card.id)
        x, y, _ = drawn(b, card, "nose")
        frames(b, [(0, x, y, True, False), (ARM_DWELL_SECONDS + .01, x, y, True, False)])
        ok, why = b.hands_idle(now=1.0)
        assert not ok and "part" in why


class TestRoutesAndTool:
    @pytest.fixture
    def client(self, rocket, monkeypatch):
        from dashboard import server
        b, card = rocket
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "t")
        monkeypatch.setattr(board_mod, "get_board", lambda: b)
        with TestClient(server.app) as c:
            c.headers["Authorization"] = "Bearer t"
            yield c, b, card

    def test_parts_route(self, client):
        c, b, card = client
        assert c.post("/api/board/parts", json={"id": card.id}).status_code == 200
        assert b._parts_mode == card.id
        assert c.post("/api/board/parts", json={"id": None}).status_code == 200
        assert b._parts_mode is None
        bad = c.post("/api/board/parts", json={"id": 5})
        assert bad.status_code == 400
        note = b.add("card", "Note")
        r = c.post("/api/board/parts", json={"id": note.id})
        assert r.status_code == 400 and "parts" in r.json()["detail"]
        assert c.post("/api/board/parts", json={"id": card.id}, headers={"Origin": "https://evil.example"}).status_code == 403

    def test_viewport_route(self, client):
        c, b, _ = client
        assert c.post("/api/board/viewport", json={"width": 1000, "height": 500}).json()["aspect"] == 2.0
        assert c.post("/api/board/viewport", json={"width": "x", "height": 1}).status_code == 400
        assert c.post("/api/board/viewport", json={"width": 0, "height": 1}).status_code == 400

    def test_the_voice_tool(self, rocket, monkeypatch):
        from agent import companion, core
        b, card = rocket
        monkeypatch.setattr(board_mod, "get_board", lambda: b)
        out = core._execute_tool("board_parts", {"on": True, "title": "rocket"})
        assert "Parts mode on" in out and b._parts_mode == card.id
        assert "off" in core._execute_tool("board_parts", {"on": False})
        assert b._parts_mode is None
        assert "Which model" in core._execute_tool("board_parts", {"on": True, "title": "chair"})
        assert "board_parts" in companion.DISCUSS_TOOLS
