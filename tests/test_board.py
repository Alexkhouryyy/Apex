"""Apex's glass board — the state, not the rendering.

The browser half needs a camera and a screen and gets neither here, so the split
is the usual one: everything that decides where a card ends up is pure and gets
exercised, and the drawing is named as unproven.

That split is only possible because the board is driven from Python. barehands
has to decide all of this inside its page, which is why none of its equivalent
logic can be tested without a webcam and a browser.
"""
import pytest

from agent.board import ARM_DWELL_SECONDS, Board, GRAB_RADIUS, HandState


def _grab_now(b, cursors, t=0.0):
    """Commit a grab immediately — two identical frames, arm then commit past
    ARM_DWELL_SECONDS. A single-frame pinch only arms (see TestArmDwell for
    that behaviour directly); tests that are not about the dwell itself use
    this to get past it in one call, the same way a real hand's pinch simply
    outlasts one frame."""
    b.apply_hands(cursors, now=t)
    b.apply_hands(cursors, now=t + ARM_DWELL_SECONDS + 0.01)


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
        _grab_now(b, [(0.52, 0.52, True)])
        assert c.held_by == [0]

    def test_an_open_hand_grabs_nothing(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False)])
        assert c.held_by == []

    def test_a_pinch_out_of_reach_grabs_nothing(self):
        b = Board()
        c = self._at(b, 0.1, 0.1)
        _grab_now(b, [(0.9, 0.9, True)])
        assert c.held_by == []

    def test_the_card_does_not_jump_into_your_hand(self):
        """THE feel test. Snapping the card's centre to the fingertip makes it
        leap the moment you pinch, which reads as the tracking being wrong. The
        grab has to remember WHERE on the card you took hold of it."""
        b = Board()
        c = self._at(b, 0.50, 0.50)
        _grab_now(b, [(0.56, 0.50, True)])        # grabbed 0.06 to the right
        assert c.x == pytest.approx(0.50, abs=1e-6), "it moved on the grab frame"
        b.apply_hands([(0.66, 0.50, True)])       # hand moves 0.10 further
        assert c.x == pytest.approx(0.60, abs=1e-6), "should track the delta"

    def test_releasing_lets_go(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True)])
        b.apply_hands([(0.5, 0.5, False)])
        assert c.held_by == []

    def test_hands_leaving_the_frame_release_everything(self):
        """Without this a card stays welded to a hand that is no longer there,
        and the only way to free it is to put your hand back in exactly the
        place it left."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True)])
        assert c.held_by == [0]
        b.apply_hands([])
        assert c.held_by == []

    def test_the_topmost_card_wins(self):
        """Two cards in the same place: you are reaching for the one you can
        see, which is the one added last."""
        b = Board()
        under = self._at(b, 0.5, 0.5)
        over = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True)])
        assert over.held_by == [0] and under.held_by == []

    def test_two_hands_hold_two_different_cards(self):
        b = Board()
        left = self._at(b, 0.2, 0.5)
        right = self._at(b, 0.8, 0.5)
        _grab_now(b, [(0.2, 0.5, True), (0.8, 0.5, True)])
        assert {left.held_by[0], right.held_by[0]} == {0, 1}

    def test_two_hands_on_one_object_is_a_two_handed_grab(self):
        """Not a conflict — this is how scaling and rotating begin."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True), (0.51, 0.5, True)])
        assert sorted(c.held_by) == [0, 1]

    def test_a_third_hand_cannot_join(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True), (0.51, 0.5, True), (0.52, 0.5, True)])
        assert len(c.held_by) == 2

    def test_a_card_cannot_be_dragged_off_the_glass(self):
        """Off-screen is unreachable — a card pushed past the edge could never
        be retrieved."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True)])
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


class TestArmDwell:
    """A pinch must hold for ARM_DWELL_SECONDS before it commits to a grab.

    Without this, one falsely-detected pinch frame — tracking is
    probabilistic, this happens — grabs whatever is nearest immediately. This
    is the difference between the board reading as occasionally glitchy and
    reading as haunted.
    """

    def _at(self, b, x, y):
        return b.add("card", "T", x=x, y=y)

    def test_a_single_pinch_frame_only_arms_it(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)], now=0.0)
        assert c.held_by == [], "one frame committed a grab the dwell should have held"
        assert b.hand_state(0) == HandState.ARMED

    def test_holding_the_pinch_past_the_dwell_commits_it(self):
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)], now=0.0)
        b.apply_hands([(0.5, 0.5, True)], now=ARM_DWELL_SECONDS + 0.01)
        assert c.held_by == [0]
        assert b.hand_state(0) == HandState.GRABBED

    def test_two_calls_too_close_together_do_not_commit(self):
        """Isolates the TIME half of the dwell, distinct from merely needing a
        second call to exist at all: two invocations a hair apart must still
        not commit, or the dwell is checking call count instead of duration."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)], now=0.0)
        b.apply_hands([(0.5, 0.5, True)], now=0.01)   # far under ARM_DWELL_SECONDS
        assert c.held_by == [], "committed on a gap far shorter than the dwell"

    def test_a_gap_in_the_pinch_resets_the_dwell(self):
        """Letting go and re-pinching later must not commit on the FIRST
        re-pinch call just because enough wall-clock time passed since the
        original (broken) pinch — the elapsed time has to be measured from
        the current, continuous hold, not from whenever arming last began."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, True)], now=0.0)
        b.apply_hands([(0.5, 0.5, False)], now=0.05)   # let go before the dwell
        # Re-pinch well after the ORIGINAL arm-start plus the dwell — if the
        # reset did not happen, this single call's elapsed-since-0.0 already
        # exceeds ARM_DWELL_SECONDS and would commit immediately.
        b.apply_hands([(0.5, 0.5, True)], now=0.30)
        assert c.held_by == [], "a stale arm timestamp let a fresh pinch commit immediately"

    def test_idle_before_any_pinch(self):
        b = Board()
        self._at(b, 0.5, 0.5)
        assert b.hand_state(0) == HandState.IDLE

    def test_being_out_of_reach_reports_idle_not_armed_forever(self):
        """Dwell satisfied, nothing to grab: the hand is not mid-arming
        anything, it is simply idle."""
        b = Board()
        self._at(b, 0.1, 0.1)
        b.apply_hands([(0.9, 0.9, True)], now=0.0)
        b.apply_hands([(0.9, 0.9, True)], now=ARM_DWELL_SECONDS + 0.01)
        assert b.hand_state(0) == HandState.IDLE

    def test_already_grabbed_hands_do_not_re_arm(self):
        """Once committed, an already-held hand reports GRABBED continuously —
        the dwell only ever gates the FIRST pinch, or a long hold would look
        like it kept re-arming."""
        b = Board()
        self._at(b, 0.5, 0.5)
        _grab_now(b, [(0.5, 0.5, True)])
        for i in range(5):
            b.apply_hands([(0.5, 0.5, True)])
            assert b.hand_state(0) == HandState.GRABBED


class TestOpenPalmCancel:
    """The board's escape hatch. Always available, and reverts to before the
    hold began — not wherever the drag currently sits, which is what an
    ordinary release already does."""

    def _at(self, b, x, y):
        return b.add("card", "T", x=x, y=y)

    def _model(self, b, x=0.5, y=0.5):
        return b.add("model", "Engine", src="models/engine.glb", x=x, y=y)

    def test_cancelling_a_drag_restores_the_pre_grab_position(self):
        b = Board()
        c = self._at(b, 0.30, 0.30)
        _grab_now(b, [(0.30, 0.30, True, False)])
        b.apply_hands([(0.60, 0.60, True, False)])   # dragged away
        assert c.x == pytest.approx(0.60) and c.y == pytest.approx(0.60)
        b.apply_hands([(0.60, 0.60, False, True)])    # open palm: cancel
        assert (c.x, c.y) == pytest.approx((0.30, 0.30)), \
            "cancel must undo the drag, not just stop it where it is"
        assert c.held_by == []

    def test_cancel_is_not_the_same_as_an_ordinary_release(self):
        """The one behavioural difference that justifies a separate gesture at
        all: releasing (un-pinching with a closed or neutral hand) keeps the
        current position; only the deliberate open-palm gesture undoes it."""
        b = Board()
        c = self._at(b, 0.30, 0.30)
        _grab_now(b, [(0.30, 0.30, True, False)])
        b.apply_hands([(0.60, 0.60, True, False)])
        b.apply_hands([(0.60, 0.60, False, False)])   # ordinary release
        assert (c.x, c.y) == pytest.approx((0.60, 0.60)), \
            "an ordinary release must not revert the position"

    def test_cancelling_a_two_handed_scale_restores_the_original_size(self):
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.45, 0.5, True, False), (0.55, 0.5, True, False)])
        b.apply_hands([(0.40, 0.5, True, False), (0.60, 0.5, True, False)])
        assert c.scale > 1.0, "the setup must have actually grown it"
        b.apply_hands([(0.40, 0.5, False, True), (0.60, 0.5, True, False)])
        assert c.scale == pytest.approx(1.0, abs=1e-6)
        assert c.rot == pytest.approx(0.0, abs=1e-6)
        assert c.held_by == [], "cancel must release BOTH hands, not just the one that opened"

    def test_either_hand_opening_palm_cancels_a_shared_grab(self):
        """'Always available' means either participant in a two-handed hold
        can end it — not only the one that grabbed first."""
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.45, 0.5, True, False), (0.55, 0.5, True, False)])
        b.apply_hands([(0.45, 0.5, True, False), (0.55, 0.5, False, True)])
        assert c.held_by == []
        assert c.scale == pytest.approx(1.0, abs=1e-6)

    def test_open_palm_on_a_hand_holding_nothing_does_nothing_odd(self):
        """Cancel is defined in terms of undoing a hold. A hand that opens
        while holding nothing has nothing to cancel — it simply stays idle."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.9, 0.9, False, True)])
        assert c.held_by == [] and (c.x, c.y) == (0.5, 0.5)
        assert b.hand_state(0) == HandState.IDLE

    def test_open_palm_never_arms_a_grab(self):
        """An open hand must not be mistaken for the start of a pinch — arming
        on it would let a subsequent accidental pinch grab immediately, with
        no dwell, the exact hole this whole mechanism exists to close."""
        b = Board()
        c = self._at(b, 0.5, 0.5)
        b.apply_hands([(0.5, 0.5, False, True)], now=0.0)
        b.apply_hands([(0.5, 0.5, True, False)], now=0.01)
        assert c.held_by == [], "open palm must not have pre-armed the grab"

    def test_cancel_reverts_to_before_the_grab_STARTED_not_before_the_second_hand_joined(self):
        """The pre-grab snapshot must be captured once, when the FIRST hand
        takes hold — not re-captured when a second hand joins later. A single
        drag-then-two-hand-scale sequence is the only way this distinction is
        observable: if the snapshot were retaken at the second hand's join,
        cancel would only undo the scaling, leaving the drag in place."""
        b = Board()
        c = self._model(b, x=0.30, y=0.30)
        _grab_now(b, [(0.30, 0.30, True, False)])       # one hand grabs
        b.apply_hands([(0.60, 0.60, True, False)])       # ...and drags it away
        assert (c.x, c.y) == pytest.approx((0.60, 0.60)), "setup: the drag must have moved it"
        # A second hand joins near the NEW position — this is the moment a
        # buggy re-capture would wrongly treat as "the start".
        _grab_now(b, [(0.60, 0.60, True, False), (0.62, 0.60, True, False)])
        # The first frame with both hands held only establishes the span
        # reference (see test_it_does_not_jump_on_the_second_hand_landing) —
        # a second, wider frame is what actually changes the scale.
        b.apply_hands([(0.58, 0.60, True, False), (0.64, 0.60, True, False)])
        b.apply_hands([(0.55, 0.60, True, False), (0.67, 0.60, True, False)])  # spread: scale up
        assert c.scale > 1.0, "setup: the spread must have grown it"
        b.apply_hands([(0.55, 0.60, False, True), (0.67, 0.60, True, False)])  # cancel
        assert (c.x, c.y) == pytest.approx((0.30, 0.30)), \
            "cancel undid only the scale, not the drag from before the second hand ever joined"
        assert c.scale == pytest.approx(1.0, abs=1e-6)


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


class TestBoardAuthInABrowser:
    """The board reached the way a person actually reaches it: by typing the URL.

    Every other endpoint test in this file sets `DASHBOARD_TOKEN=""`, which
    disables the auth middleware entirely — and that is precisely why `/board`
    shipped answering **401 to a browser**. A browser navigating to a URL cannot
    attach an `Authorization: Bearer` header; nothing can. So the one client the
    page exists for was the one client never exercised, and the suite was green
    the whole time.

    Everything here runs with a REAL token set and NO header, because that is the
    only configuration in which the bug is visible.
    """

    TOKEN = "board-test-token"

    def _client(self, monkeypatch):
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", self.TOKEN, raising=False)
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "BOARD_FPS", 60, raising=False)
        # The middleware throttles repeated bad tokens per IP, and every test
        # client shares one. Without this, a test that deliberately fails auth
        # ten times would start handing 429s to unrelated tests.
        server._throttle.reset("testclient")
        return TestClient(server.app)

    def test_a_browser_can_open_the_board(self, monkeypatch):
        """THE regression test. No Authorization header, token configured.

        Revert the `/board` exemption in dashboard/server.py's `_auth` and this
        returns 401 — which is exactly what the laptop's browser showed.
        """
        r = self._client(monkeypatch).get("/board")
        assert r.status_code == 200, \
            "a browser cannot send a bearer header; the shell must load without one"
        assert "Apex" in r.text

    def test_the_exemption_does_not_leak_the_props_route(self, monkeypatch, tmp_path):
        """The shell is exempt; the files it loads are not.

        Written as an exact-match check on purpose: `path.startswith("/board")`
        would look like the same fix and would serve every prop to anyone who
        asked, with no token at all.

        A REAL prop sits behind the route, not a missing one. Against a missing
        file the leaky version answers 404, so the test would still fail — but
        for the wrong reason, and it would go on passing the day someone put a
        file there. With real bytes present, the failure is the actual harm: the
        file came back.
        """
        from agent import props
        (tmp_path / "engine.glb").write_bytes(b"glTF-SECRET-BYTES")
        monkeypatch.setattr(props, "props_root", lambda: tmp_path)
        r = self._client(monkeypatch).get("/board/prop/engine.glb")
        assert b"SECRET-BYTES" not in r.content, "the prop was served with no token"
        assert r.status_code == 401, "props must stay behind the token"

    def test_the_rest_of_the_dashboard_is_still_shut(self, monkeypatch):
        """Proof the exemption is one path and not a hole in the middleware."""
        c = self._client(monkeypatch)
        for path in ("/api/status", "/api/devices"):
            assert c.get(path).status_code in (401, 429), path

    def test_the_socket_accepts_the_configured_token(self, monkeypatch):
        """The shell being open is only useful if the data path then opens too —
        otherwise the fix trades a 401 page for a blank one."""
        from agent.board import get_board
        get_board().clear()
        get_board().add("card", "AUTHORIZED", "")
        c = self._client(monkeypatch)
        with c.websocket_connect(f"/ws/board?token={self.TOKEN}") as ws:
            msg = ws.receive_json()
        assert [x["title"] for x in msg["cards"]] == ["AUTHORIZED"]
        get_board().clear()

    def test_the_socket_refuses_a_page_that_has_no_token(self, monkeypatch):
        """Opening the shell must not be the same as being logged in."""
        c = self._client(monkeypatch)
        with pytest.raises(Exception):
            with c.websocket_connect("/ws/board") as ws:
                ws.receive_json()


class TestBoardPageCredentials:
    """What the page does about the token, read out of the page itself.

    Stated plainly: these are source assertions, not behaviour. Nothing here runs
    JavaScript, so they can prove the code is present and cannot prove it works —
    that is the browser half, and it stays the browser's to prove. They exist
    because both defects below are invisible to every other test in this file,
    and both were shipped.
    """

    def _page(self):
        """The page with every comment stripped.

        Load-bearing. The first version of the 1008 assertion below searched the
        raw file and passed while reading nothing but the comment that explains
        the branch — so deleting the branch left it green. A test that a comment
        can satisfy is testing the comment.
        """
        import re
        from pathlib import Path
        src = Path("dashboard/static/board.html").read_text(encoding="utf-8")
        src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
        return src

    def test_the_page_falls_back_to_the_stored_token(self):
        """`?token=` alone means typing /board in the address bar connects to
        nothing. The dashboard already stores the credential under `apex_token`
        on the same origin, so the page should read it rather than demand a
        hand-built URL."""
        page = self._page()
        assert "apex_token" in page and "localStorage" in page, \
            "the board must reuse the token the dashboard stored"

    def test_a_refused_token_is_not_reported_as_a_reconnect(self):
        """1008 is the close code for a bad token. Retrying it renders as
        'reconnecting…', which is identical to Apex being down — so a wrong
        password would look like a crash, forever."""
        page = self._page()
        assert "1008" in page, \
            "a rejected token must be told apart from a dropped connection"
        assert "ws.onclose = () =>" not in page, \
            "a close handler that ignores its close code cannot tell them apart"


class TestApexBoardTools:
    def test_board_present_lands_on_apexs_own_board(self, monkeypatch):
        """board_present always uses Apex's own board — there is no second
        program it could fall back to reaching."""
        import config
        from agent import core
        from agent.board import get_board
        monkeypatch.setattr(config, "BOARD_ENABLED", True, raising=False)
        get_board().clear()
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
        _grab_now(b, [(0.45, 0.5, True), (0.55, 0.5, True)])     # span 0.10
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])    # span 0.20
        assert c.scale == pytest.approx(2.0, abs=0.01)

    def test_hands_coming_together_scale_it_down(self):
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.40, 0.5, True), (0.60, 0.5, True)])     # span 0.20
        b.apply_hands([(0.45, 0.5, True), (0.55, 0.5, True)])    # span 0.10
        assert c.scale == pytest.approx(0.5, abs=0.01)

    def test_it_does_not_jump_on_the_second_hand_landing(self):
        """THE feel test for two hands. Without a remembered starting span the
        object leaps to whatever scale the current hand distance implies, the
        instant the second hand arrives."""
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.30, 0.5, True), (0.70, 0.5, True)])
        assert c.scale == pytest.approx(1.0, abs=1e-6), "it resized on grab"

    def test_turning_your_hands_rotates_it(self):
        import math
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.45, 0.5, True), (0.55, 0.5, True)])     # flat
        b.apply_hands([(0.5, 0.45, True), (0.5, 0.55, True)])    # quarter turn
        assert abs(c.rot) == pytest.approx(math.pi / 2, abs=0.05)

    def test_scale_is_clamped_at_both_ends(self):
        """Scaled to nothing it cannot be grabbed again; scaled past the screen
        it cannot be seen. Neither has an undo."""
        from agent.board import MAX_SCALE, MIN_SCALE
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.499, 0.5, True), (0.501, 0.5, True)])
        b.apply_hands([(0.0, 0.5, True), (1.0, 0.5, True)])
        assert c.scale <= MAX_SCALE
        b2 = Board()
        c2 = self._model(b2)
        _grab_now(b2, [(0.0, 0.5, True), (1.0, 0.5, True)])
        b2.apply_hands([(0.4999, 0.5, True), (0.5001, 0.5, True)])
        assert c2.scale >= MIN_SCALE

    def test_dropping_to_one_hand_goes_back_to_dragging(self):
        b = Board()
        c = self._model(b)
        _grab_now(b, [(0.45, 0.5, True), (0.55, 0.5, True)])
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
        _grab_now(b, [(0.45, 0.5, True), (0.55, 0.5, True)])
        b.apply_hands([(0.40, 0.5, True), (0.60, 0.5, True)])
        assert c.scale == pytest.approx(2.0, abs=0.01)
        b.apply_hands([])                                        # let go
        _grab_now(b, [(0.45, 0.5, True), (0.55, 0.5, True)])     # grab again
        assert c.scale == pytest.approx(2.0, abs=0.01)
