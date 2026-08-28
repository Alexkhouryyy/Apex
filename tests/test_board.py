"""Apex's glass board — the state, not the rendering.

The browser half needs a camera and a screen and gets neither here, so the split
is the usual one: everything that decides where a card ends up is pure and gets
exercised, and the drawing is named as unproven.

That split is only possible because the board is driven from Python. barehands
has to decide all of this inside its page, which is why none of its equivalent
logic can be tested without a webcam and a browser.
"""
import pytest

from agent.board import Board, GRAB_RADIUS


class TestContent:
    def test_a_card_lands_where_it_was_put(self):
        b = Board()
        c = b.add("card", "HELLO", "body", x=0.3, y=0.7)
        assert b.cards()[0]["title"] == "HELLO"
        assert (c.x, c.y) == (0.3, 0.7)

    def test_the_oldest_card_falls_off_the_end(self):
        """A board that grows without limit stops being usable long before it
        stops being fast."""
        b = Board(max_cards=3)
        for i in range(5):
            b.add("card", f"card {i}")
        titles = [c["title"] for c in b.cards()]
        assert titles == ["card 2", "card 3", "card 4"]

    def test_clear_reports_what_it_removed(self):
        b = Board()
        b.add("card", "a"); b.add("card", "b")
        assert b.clear() == 2 and b.count() == 0

    def test_removing_a_card_that_is_not_there_says_so(self):
        assert Board().remove("nope") is False


class TestGrabbing:
    def _at(self, b, x, y):
        return b.add("card", "T", x=x, y=y)

    def test_a_pinch_near_a_card_picks_it_up(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.52, 0.52, True)])
        assert c.held_by == [0]

    def test_an_open_hand_grabs_nothing(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False)])
        assert c.held_by == []

    def test_a_pinch_out_of_reach_grabs_nothing(self):
        b = Board()
        c = self._at(b, 0.1, 0.1)
        b.apply_hands([(0.9, 0.9, True)])
        assert c.held_by == []

    def test_the_card_does_not_jump_into_your_hand(self):
        """THE feel test. Snapping the card's centre to the fingertip makes it
        leap the moment you pinch, which reads as the tracking being wrong. The
        grab has to remember WHERE on the card you took hold of it."""
        b = Board()
        c = self._at(b, 0.50, 0.50)
        b.apply_hands([(0.56, 0.50, True)])       # grabbed 0.06 to the right
        assert c.x == pytest.approx(0.50, abs=1e-6), "it moved on the grab frame"
        b.apply_hands([(0.66, 0.50, True)])       # hand moves 0.10 further
        assert c.x == pytest.approx(0.60, abs=1e-6), "should track the delta"

    def test_releasing_lets_go(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)])
        b.apply_hands([(0.5, 0.5, False)])
        assert c.held_by == []

    def test_hands_leaving_the_frame_release_everything(self):
        """Without this a card stays welded to a hand that is no longer there,
        and the only way to free it is to put your hand back in exactly the
        place it left."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)])
        assert c.held_by == [0]
        b.apply_hands([])
        assert c.held_by == []

    def test_the_topmost_card_wins(self):
        """Two cards in the same place: you are reaching for the one you can
        see, which is the one added last."""
        b = Board()
        under = self._at(b, 0.5, 0.5)
        over = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)])
        assert over.held_by == [0] and under.held_by == []

    def test_two_hands_hold_two_different_cards(self):
        b = Board()
        left = self._at(b, 0.2, 0.5)
        right = self._at(b, 0.8, 0.5)
        b.apply_hands([(0.2, 0.5, True), (0.8, 0.5, True)])
        assert {left.held_by[0], right.held_by[0]} == {0, 1}

    def test_two_hands_on_one_object_is_a_two_handed_grab(self):
        """Not a conflict — this is how scaling and rotating begin."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True), (0.51, 0.5, True)])
        assert sorted(c.held_by) == [0, 1]

    def test_a_third_hand_cannot_join(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True), (0.51, 0.5, True), (0.52, 0.5, True)])
        assert len(c.held_by) == 2

    def test_a_card_cannot_be_dragged_off_the_glass(self):
        """Off-screen is unreachable — a card pushed past the edge could never
        be retrieved."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)])
        b.apply_hands([(5.0, -3.0, True)])
        assert 0.0 <= c.x <= 1.0 and 0.0 <= c.y <= 1.0

    @pytest.mark.parametrize("junk", [
        [None], [(0.5,)], [("a", "b", "c")], [{}], "not cursors",
    ])
    def test_malformed_cursors_do_not_raise(self, junk):
        """This is driven from the tracker thread; a raise would take hand
        tracking down with the board."""
        b = Board()
        b.add("card", "T")
        b.apply_hands(junk)

    def test_the_grab_radius_is_reachable_in_practice(self):
        """Too tight a radius makes reaching for a card feel like the tracking
        is broken rather than like a miss."""
        assert 0.08 <= GRAB_RADIUS <= 0.25


# ── The transport, against a real server ─────────────────────────────────────

class TestBoardEndpoints:
    """The page and the socket, exercised through a real HTTP server rather
    than by asserting the route table has the right strings in it.

    What is NOT covered: pixels. Nothing here has a screen. What IS covered is
    every decision the browser is not allowed to make — which is all of them,
    since the page draws and decides nothing.
    """

    def _client(self, monkeypatch):
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "BOARD_FPS", 60, raising=False)
        return TestClient(server.app)

    def test_the_page_is_served(self, monkeypatch):
        r = self._client(monkeypatch).get("/board")
        assert r.status_code == 200
        assert "Apex" in r.text

    def test_the_page_never_loads_mediapipe(self, monkeypatch):
        """THE architectural assertion. The moment this page runs its own
        tracker it inherits the failure the whole design exists to avoid:
        tracking that stops when the tab is backgrounded.

        Comments are stripped first. The first version of this failed on the
        page's own comment explaining that it does NOT run MediaPipe — an
        assertion so blunt it could not tell using a thing from saying you do
        not use it.
        """
        import re
        body = self._client(monkeypatch).get("/board").text
        code = re.sub(r"<!--.*?-->", "", body, flags=re.S).lower()
        for forbidden in ("mediapipe", "getusermedia", "handlandmarker",
                          "filesetresolver"):
            assert forbidden not in code, \
                f"the board must not do its own {forbidden}"

    def test_the_socket_streams_cards(self, monkeypatch):
        from agent.board import get_board
        get_board().clear()
        get_board().add("card", "FROM A TEST", "body text")
        with self._client(monkeypatch).websocket_connect("/ws/board") as ws:
            msg = ws.receive_json()
        assert [c["title"] for c in msg["cards"]] == ["FROM A TEST"]
        get_board().clear()

    def test_tracking_off_is_stated_not_inferred(self, monkeypatch):
        """A tracker that is OFF and a tracker seeing NO HANDS both send an
        empty cursor list. The page must be able to tell them apart, because one
        is fixed in .env and the other by raising your hand."""
        from agent import handtrack
        handtrack.set_active_tracker(None)
        with self._client(monkeypatch).websocket_connect("/ws/board") as ws:
            msg = ws.receive_json()
        assert msg["tracking"] is False
        assert msg["cursors"] == []

    def test_cursors_and_frames_come_from_the_tracker(self, monkeypatch):
        from agent import handtrack

        class _Fake:
            def latest_cursors(self): return [(0.25, 0.75, True)]
            def latest_jpeg(self): return b"\xff\xd8jpegbytes"

        handtrack.set_active_tracker(_Fake())
        try:
            with self._client(monkeypatch).websocket_connect("/ws/board") as ws:
                msg = ws.receive_json()
        finally:
            handtrack.set_active_tracker(None)
        assert msg["tracking"] is True
        assert msg["cursors"] == [{"x": 0.25, "y": 0.75, "p": 1}]
        assert msg["frame"], "the backdrop must travel from Python"

    def test_the_socket_requires_the_token_when_one_is_set(self, monkeypatch):
        """/ws/live had to check this by hand because HTTP middleware does not
        run on websocket upgrades. The same hole exists here."""
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "secret", raising=False)
        client = TestClient(server.app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/board?token=wrong") as ws:
                ws.receive_json()


class TestApexBoardTools:
    def test_board_present_uses_apexs_own_board_when_it_is_on(self, monkeypatch):
        """With BOARD_ENABLED the card must land on Apex's board, not be posted
        to a barehands server that may not be running."""
        import config
        from agent import core
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        get_board().clear()

        def _no_network(*a, **k):
            raise AssertionError("must not reach barehands")
        monkeypatch.setattr("tools.barehands.board_command", _no_network)

        out = core._execute_tool("board_present", {"title": "HI", "body": "there"})
        assert "on the board" in out
        assert get_board().count() == 1
        get_board().clear()

    def test_board_state_reads_apexs_own_board(self, monkeypatch):
        import config
        from agent import core
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        get_board().clear()
        get_board().add("card", "ON THE GLASS")
        assert "ON THE GLASS" in core._execute_tool("board_state", {})
        get_board().clear()

    def test_board_clear_empties_it(self, monkeypatch):
        import config
        from agent import core
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        get_board().add("card", "x"); get_board().add("card", "y")
        assert "2" in core._execute_tool("board_clear", {})
        assert get_board().count() == 0

    def test_barehands_still_works_when_apexs_board_is_off(self, monkeypatch):
        """Adding a board of our own must not break the one that already
        worked."""
        import config
        from agent import core
        monkeypatch.setattr(config, "BOARD_ENABLED", False, raising=False)
        seen = []
        monkeypatch.setattr("tools.barehands.board_command",
                            lambda cmd: seen.append(cmd) or "[Barehands] ok")
        core._execute_tool("board_present", {"title": "HI"})
        assert seen and seen[0]["title"] == "HI"


# ── 3D models, and the two-handed grab ───────────────────────────────────────

class TestModels:
    """One hand drags; two hands scale and rotate.

    This is the touchscreen pinch-to-zoom everyone already knows, lifted to two
    hands — chosen over barehands' hold-still-to-rotate on purpose. Theirs is
    their interaction design, tuned over weeks; this one is discoverable without
    being taught.
    """

    def _model(self, b, x=0.5, y=0.5):
        return b.add("model", "Engine", src="models/engine.glb", x=x, y=y)

    def test_a_model_carries_its_prop_path(self):
        b = Board()
        c = self._model(b)
        d = b.cards()[0]
        assert d["kind"] == "model" and d["src"] == "models/engine.glb"

    def test_hands_spreading_apart_scale_it_up(self):
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])   # span 0.10
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])   # span 0.20
        assert c.scale == pytest.approx(2.0, abs=0.01)

    def test_hands_coming_together_scale_it_down(self):
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])   # span 0.20
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])   # span 0.10
        assert c.scale == pytest.approx(0.5, abs=0.01)

    def test_it_does_not_jump_on_the_second_hand_landing(self):
        """THE feel test for two hands. Without a remembered starting span the
        object leaps to whatever scale the current hand distance implies, the
        instant the second hand arrives."""
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.30, 0.5, True), (0.70, 0.5, True)])
        assert c.scale == pytest.approx(1.0, abs=1e-6), "it resized on grab"

    def test_turning_your_hands_rotates_it(self):
        import math
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])   # flat
        b.apply_hands([(0.5, 0.45, True), (0.5, 0.55, True)])   # quarter turn
        assert abs(c.rot) == pytest.approx(math.pi / 2, abs=0.05)

    def test_scale_is_clamped_at_both_ends(self):
        """Scaled to nothing it cannot be grabbed again; scaled past the screen
        it cannot be seen. Neither has an undo."""
        from agent.board import MAX_SCALE, MIN_SCALE
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.499, 0.5, True), (0.501, 0.5, True)])
        b.apply_hands([(0.0, 0.5, True), (1.0, 0.5, True)])
        assert c.scale <= MAX_SCALE
        b2 = Board()
        c2 = self._model(b2)
        b2.apply_hands([(0.0, 0.5, True), (1.0, 0.5, True)])
        b2.apply_hands([(0.4999, 0.5, True), (0.5001, 0.5, True)])
        assert c2.scale >= MIN_SCALE

    def test_dropping_to_one_hand_goes_back_to_dragging(self):
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])
        grown = c.scale
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, False)])  # right hand opens
        b.apply_hands([(0.30, 0.5, True), (0.60, 0.5, False)])  # left drags
        assert len(c.held_by) == 1
        assert c.scale == pytest.approx(grown, abs=1e-6), "letting go resized it"
        assert c.x < 0.5, "it should have moved with the remaining hand"

    def test_re_grabbing_scales_from_where_it_was_left(self):
        """A second two-handed grab must continue from the current size, not
        reset to 1.0 — otherwise every regrab throws away your work."""
        b = Board()
        c = self._model(b)
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])
        assert c.scale == pytest.approx(2.0, abs=0.01)
        b.apply_hands([])                                        # let go
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])    # grab again
        assert c.scale == pytest.approx(2.0, abs=0.01)
