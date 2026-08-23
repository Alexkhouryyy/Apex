"""The barehands bridge — the ring, the board, and the watcher's poll loop.

Split the way `tests/test_mcp_client.py` splits: the halves that decide whether
anything works at all are exercised here, and the half that needs a webcam is
named as unproven rather than assumed. Concretely, what is NOT covered is real
fingers producing real cursor frames in Chrome — MediaPipe runs in the browser,
so nothing on a CI box can reach it. Everything up to and including the HTTP
surface is real, including a test that boots the actual barehands server when
the repo happens to be present.

The load-bearing test here is `test_a_queued_command_does_not_claim_success`.
barehands returns 204 for a command nobody will ever see, which is the exact
fail-open shape this codebase has produced eighteen times: the call succeeds,
the card never appears, and nothing anywhere says why.
"""
import json
import threading
import time

import pytest

import config
from agent import barehands_watcher as bh
from tools import barehands


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(config, "BAREHANDS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "BAREHANDS_URL", "http://127.0.0.1:8794",
                        raising=False)
    barehands.set_tracker_probe(None)
    yield
    barehands.set_tracker_probe(None)


@pytest.fixture
def ring(tmp_path, monkeypatch):
    """A barehands checkout's state/ folder, without a barehands checkout."""
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(config, "BAREHANDS_DIR", str(tmp_path), raising=False)
    return tmp_path / "state"


# ── The ring is a face ───────────────────────────────────────────────────────

class TestRing:
    @pytest.mark.parametrize("word", barehands.RING_STATES)
    def test_each_ring_state_is_written_as_one_bare_word(self, ring, word):
        """barehands reads this file and compares against a literal set, so a
        trailing newline or a capital letter silently means 'idle'."""
        barehands.publish_state(word)
        assert (ring / "state").read_text() == word

    def test_the_four_words_match_residentstate_exactly(self):
        """If these ever drift, the ring silently stops following Apex — the
        write succeeds and barehands ignores the value."""
        from app.resident import ResidentState
        assert set(barehands.RING_STATES) == {
            ResidentState.IDLE, ResidentState.LISTENING,
            ResidentState.THINKING, ResidentState.SPEAKING,
        }

    def test_muted_becomes_idle_plus_amber(self, ring):
        """MUTED has no barehands equivalent. Dropping it would leave the ring
        showing whatever it showed last, which reads as 'still listening'."""
        barehands.publish_state("muted")
        assert (ring / "state").read_text() == "idle"
        assert json.loads((ring / "mood.json").read_text())["mood"] == "amber"

    def test_an_unknown_state_is_refused_not_written(self, ring):
        out = barehands.publish_state("pondering")
        assert "not one of" in out
        assert not (ring / "state").exists()

    def test_a_missing_barehands_dir_is_announced(self, monkeypatch):
        """THE reason state_dir() validates instead of assuming: a wrong path
        makes every write raise into a try/except and the ring just never moves,
        with nothing printed anywhere."""
        monkeypatch.setattr(config, "BAREHANDS_DIR", "/nope/not/here",
                            raising=False)
        assert "BAREHANDS_DIR" in barehands.publish_state("thinking")

    def test_disabled_writes_nothing(self, ring, monkeypatch):
        monkeypatch.setattr(config, "BAREHANDS_ENABLED", False, raising=False)
        barehands.publish_state("thinking")
        assert not (ring / "state").exists()


class TestRingFollowsResident:
    def test_the_ring_mirrors_state_changes(self, ring):
        from app.resident import ResidentState
        state = ResidentState()
        barehands.attach_to_resident_state(state)
        state.set(ResidentState.THINKING)
        assert (ring / "state").read_text() == "thinking"
        state.set(ResidentState.SPEAKING)
        assert (ring / "state").read_text() == "speaking"

    def test_the_current_state_is_published_at_attach_time(self, ring):
        """set() fires listeners only on a CHANGE, so without this the ring sits
        on a stale file until Apex next happens to change state — which, when
        barehands starts after Apex, is indefinitely."""
        from app.resident import ResidentState
        state = ResidentState()
        state.set(ResidentState.LISTENING)
        assert not (ring / "state").exists()
        barehands.attach_to_resident_state(state)
        assert (ring / "state").read_text() == "listening"

    def test_the_listener_reads_get_not_its_argument(self, ring):
        """Mute lives outside the state machine and is applied in get(), so a
        listener that trusts its argument reports 'listening' while Apex is
        muted."""
        from app.resident import ResidentState
        state = ResidentState()
        barehands.attach_to_resident_state(state)
        state.mute(-1)
        state.set(ResidentState.LISTENING)
        assert (ring / "state").read_text() == "idle"


# ── The board is a stage ─────────────────────────────────────────────────────

class TestBoardCommands:
    def test_an_action_outside_the_allowlist_never_leaves_the_process(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("must not reach the network")
        monkeypatch.setattr(barehands.urllib.request, "urlopen", _boom)
        assert "not a board action" in barehands.board_command({"a": "rm -rf"})

    def test_a_dark_server_says_so(self, monkeypatch):
        monkeypatch.setattr(barehands, "tracker_status",
                            lambda: bh.TRACKER_DOWN)
        assert "isn't running" in barehands.board_command({"a": "add_card"})

    def test_a_queued_command_does_not_claim_success(self, monkeypatch):
        """THE load-bearing test.

        barehands returns 204 for a command queued against a tracker that does
        not exist — the card is queued into the void and the HTTP call looks
        like a win. Verified against a real server:
            POST /cmd -> 204   while   GET /state -> {}
        Delete the TRACKER_NO_STAGE branch in board_command and this fails.
        """
        monkeypatch.setattr(barehands, "tracker_status",
                            lambda: bh.TRACKER_NO_STAGE)
        monkeypatch.setattr(barehands, "_last_send", 0.0, raising=False)

        class _Resp:
            def getcode(self): return 204
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(barehands.urllib.request, "urlopen",
                            lambda *a, **k: _Resp())

        out = barehands.board_command({"a": "add_card", "title": "HI"})
        assert "queued" in out and "no stage is open" in out
        assert "is on the board" not in out

    def test_a_frozen_stage_does_not_claim_success(self, monkeypatch):
        monkeypatch.setattr(barehands, "tracker_status",
                            lambda: bh.TRACKER_FROZEN)
        monkeypatch.setattr(barehands, "_last_send", 0.0, raising=False)

        class _Resp:
            def getcode(self): return 204
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(barehands.urllib.request, "urlopen",
                            lambda *a, **k: _Resp())
        assert "frozen" in barehands.board_command({"a": "present"})

    def test_a_live_stage_reports_plainly(self, monkeypatch):
        monkeypatch.setattr(barehands, "tracker_status",
                            lambda: bh.TRACKER_LIVE)
        monkeypatch.setattr(barehands, "_last_send", 0.0, raising=False)

        class _Resp:
            def getcode(self): return 204
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(barehands.urllib.request, "urlopen",
                            lambda *a, **k: _Resp())
        assert "is on the board" in barehands.board_command({"a": "add_card"})

    def test_the_watcher_answers_liveness_when_it_is_running(self):
        """Only the watcher has two samples over time, so only it can tell a
        live board from a frozen one."""
        barehands.set_tracker_probe(lambda: bh.TRACKER_FROZEN)
        assert barehands.tracker_status() == bh.TRACKER_FROZEN


class TestBoardState:
    def test_no_stage_is_distinguished_from_an_empty_board(self, monkeypatch):
        monkeypatch.setattr(barehands, "_get", lambda *a, **k: b"{}")
        assert "no stage has connected" in barehands.board_state()

    def test_an_empty_board_says_empty(self, monkeypatch):
        monkeypatch.setattr(barehands, "_get",
                            lambda *a, **k: json.dumps({"cursors": [], "items": []}).encode())
        assert "empty" in barehands.board_state()

    def test_items_are_listed_with_what_is_in_your_hand(self, monkeypatch):
        payload = {"cursors": [{"x": 0.5, "y": 0.5, "p": 1, "d": 0}],
                   "items": [{"type": "card", "title": "THE PLAN", "g": 1},
                             {"type": "img", "src": "/media/misc/logo.png"}]}
        monkeypatch.setattr(barehands, "_get",
                            lambda *a, **k: json.dumps(payload).encode())
        out = barehands.board_state()
        assert "THE PLAN" in out and "in your hand" in out
        assert "logo.png" in out and "1 hand(s)" in out

    @pytest.mark.parametrize("payload", [
        b"not json", b"[]", b'{"items": "not a list"}',
        json.dumps({"cursors": [], "items": ["not a dict"]}).encode(),
    ])
    def test_garbage_does_not_raise(self, monkeypatch, payload):
        """core dispatches straight into this; a raise would end the turn."""
        monkeypatch.setattr(barehands, "_get", lambda *a, **k: payload)
        barehands.board_state()


# ── The watcher's loop ───────────────────────────────────────────────────────

class _FakeLog:
    def __init__(self):
        self.events = []

    def add(self, source, content):
        self.events.append((source, content))


class TestWatcherLoop:
    def test_a_recognized_gesture_reaches_the_awareness_log(self, monkeypatch):
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS", ["wave:wake"],
                            raising=False)
        log = _FakeLog()
        fired = []
        w = bh.BarehandsWatcher(log, on_gesture=lambda g, a: fired.append((g, a)))
        frames = []
        for _ in range(4):
            for x in (0.40, 0.48, 0.56, 0.48):
                frames.append(json.dumps({
                    "cursors": [{"x": x, "y": 0.5, "p": 0, "d": 0}],
                    "items": []}).encode())
        for i, body in enumerate(frames):
            monkeypatch.setattr(w, "fetch", lambda b=body: b)
            w._tick(1000.0 + i * 0.05)
        assert any("wave" in c for _s, c in log.events)
        assert ("wave", "wake") in fired

    def test_an_unmapped_gesture_is_logged_but_powerless(self, monkeypatch):
        """Recognition and action are separate gates. If an unmapped gesture
        were dropped entirely, a misconfigured allowlist and a broken
        recognizer would look identical from outside."""
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS", [], raising=False)
        log = _FakeLog()
        fired = []
        w = bh.BarehandsWatcher(log, on_gesture=lambda g, a: fired.append(g))
        monkeypatch.setattr(w, "fetch", lambda: json.dumps({
            "cursors": [{"x": 0.5, "y": 0.5, "p": 0, "d": 0}], "items": []}).encode())
        w._tick(1000.0)
        assert any("hands are up" in c for _s, c in log.events)
        assert fired == []

    def test_a_raising_gesture_handler_does_not_kill_the_watcher(self, monkeypatch):
        """This runs in a poll thread — a raise ends hand tracking silently for
        the rest of the session."""
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS",
                            ["hands_present:wake"], raising=False)
        log = _FakeLog()

        def _boom(g, a):
            raise RuntimeError("handler exploded")
        w = bh.BarehandsWatcher(log, on_gesture=_boom)
        monkeypatch.setattr(w, "fetch", lambda: json.dumps({
            "cursors": [{"x": 0.5, "y": 0.5, "p": 0, "d": 0}], "items": []}).encode())
        w._tick(1000.0)          # must not raise

    def test_an_unreachable_server_is_survivable(self, monkeypatch):
        log = _FakeLog()
        w = bh.BarehandsWatcher(log)
        monkeypatch.setattr(w, "fetch", lambda: None)
        w._tick(1000.0)
        assert w.recognizer.tracker == bh.TRACKER_DOWN

    def test_the_poll_rate_is_fast_enough_for_a_wave(self):
        """A natural wave runs 2-3 Hz. At 10 Hz a 3 Hz wave gives ~3.3 samples
        per cycle and the reversals alias away, so fast waves silently stop
        being detected. This pins the default rather than leaving it to taste."""
        log = _FakeLog()
        w = bh.BarehandsWatcher(log)
        samples_per_cycle = (1.0 / w.interval) / bh.WAVE_MAX_HZ
        assert samples_per_cycle >= 2 * (bh.WAVE_MIN_REVERSALS - 1), (
            f"{1 / w.interval:.0f} Hz gives {samples_per_cycle:.1f} samples per "
            f"{bh.WAVE_MAX_HZ} Hz cycle — not enough to see the reversals")


# ── The real transport, with a synthetic hand ────────────────────────────────

@pytest.fixture
def live_server(tmp_path):
    """The actual barehands server, if the repo is on this machine.

    Not a mock of the protocol — the protocol itself, including the 204-for-a-
    void behaviour that motivated board_command. `server.py` resolves everything
    relative to its own file and reads its port from a sibling barehands.json,
    so copying just that one file into a tmp dir gives a real server on a free
    port with no media jail to worry about. Skipped when the repo is absent,
    which is most machines.
    """
    import shutil
    import socket
    import subprocess
    from pathlib import Path

    for candidate in (Path.home() / "barehands",
                      Path("/tmp/claude-0/-home-user-ni/"
                           "d6e7e388-9597-5aaf-bd7c-f9281fd45bd5/scratchpad/barehands")):
        if (candidate / "server.py").is_file():
            repo = candidate
            break
    else:
        pytest.skip("barehands checkout not present")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    shutil.copy(repo / "server.py", tmp_path / "server.py")
    (tmp_path / "barehands.json").write_text(
        json.dumps({"name": "Apex", "port": port, "orbs": []}))

    proc = subprocess.Popen(["python3", "server.py"], cwd=str(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        if proc.poll() is not None:
            pytest.skip(f"barehands server exited: "
                        f"{(proc.stdout.read() or b'').decode()[:200]}")
        try:
            barehands.urllib.request.urlopen(f"{base}/state", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.skip("barehands server did not come up")
    yield base
    proc.kill()
    proc.wait(timeout=5)


class TestAgainstTheRealServer:
    def test_the_protocol_is_what_we_think_it_is(self, live_server, monkeypatch):
        """Everything else in this file trusts a shape read out of stage.html.
        This is the one test that would notice if that shape were wrong or if
        barehands changed it under us."""
        monkeypatch.setattr(config, "BAREHANDS_URL", live_server, raising=False)

        # No tracker has ever connected — a real state, not an error.
        assert barehands.tracker_status() == bh.TRACKER_NO_STAGE
        assert "no stage has connected" in barehands.board_state()

        # A command against no tracker: 204, and queued into the void.
        out = barehands.board_command({"a": "add_card", "title": "HELLO"})
        assert "queued" in out, out

        # Now play the tracker: POST a scene, exactly as stage.html does.
        scene = json.dumps({
            "cursors": [{"x": 0.5, "y": 0.4, "p": 1, "d": 0}],
            "items": [{"type": "card", "title": "THE PLAN", "g": 1,
                       "x": 0.5, "y": 0.5, "scale": 1.0}],
        }).encode()
        req = barehands.urllib.request.Request(
            f"{live_server}/state", data=scene, method="POST")
        with barehands.urllib.request.urlopen(req, timeout=3) as r:
            queued = json.loads(r.read())
        assert any(c.get("a") == "add_card" for c in queued), (
            "the queued command should ride back on the tracker's heartbeat")

        # And now Apex can see the board through the real endpoint.
        assert barehands.tracker_status() == bh.TRACKER_LIVE
        seen = barehands.board_state()
        assert "THE PLAN" in seen and "in your hand" in seen

    def test_the_watcher_recognizes_a_swipe_through_the_real_server(
            self, live_server, monkeypatch):
        """The full inbound path with only the webcam replaced: real HTTP, real
        server, synthetic hand."""
        monkeypatch.setattr(config, "BAREHANDS_URL", live_server, raising=False)
        monkeypatch.setattr(config, "BAREHANDS_GESTURE_ACTIONS",
                            ["swipe_right:wake"], raising=False)
        log = _FakeLog()
        w = bh.BarehandsWatcher(log, url=live_server)
        for i in range(16):
            scene = json.dumps({
                "cursors": [{"x": 0.1 + i * 0.05, "y": 0.5, "p": 0, "d": 0}],
                "items": []}).encode()
            req = barehands.urllib.request.Request(
                f"{live_server}/state", data=scene, method="POST")
            barehands.urllib.request.urlopen(req, timeout=3).read()
            w._tick(1000.0 + i * 0.05)
        assert any("swipe_right" in c for _s, c in log.events), log.events
