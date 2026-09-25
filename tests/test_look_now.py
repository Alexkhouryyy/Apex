"""Look now: Ctrl+Alt+C / "Hey Celly" -> Celine looks at the screen and helps."""
from __future__ import annotations

import base64
import importlib.util
import io
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

import config
from agent import look_now

ROOT = Path(__file__).resolve().parents[1]


def jpeg(size=(64, 40)):
    out = io.BytesIO()
    Image.new("RGB", size, "green").save(out, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


@pytest.fixture(autouse=True)
def clean():
    look_now.reset()
    yield
    look_now.reset()


class TestTheWakePhrase:

    @pytest.mark.parametrize("heard,question", [
        ("Hey Celly, why is this test failing?", "why is this test failing"),
        ("hey kelly what does this error mean", "what does this error mean"),
        ("Hey Celly.", ""),
        ("hey celly", ""),
    ])
    def test_what_follows_the_phrase_is_the_question(self, heard, question):
        assert look_now.question_from(heard, config.CELINE_WAKE_PHRASES) == question

    def test_the_usual_mishearings_are_accepted(self):
        from voice.wake import matches_wake_phrase
        for heard in ("hey celly", "Hey Kelly, look", "hey shelly", "hey selly", "hey celine"):
            assert matches_wake_phrase(heard, config.CELINE_WAKE_PHRASES), heard
        assert not matches_wake_phrase("they sell it cheap", config.CELINE_WAKE_PHRASES)


class TestTheQueue:

    def test_a_request_waits_for_the_page_without_its_image(self):
        r = look_now.request("hotkey", image=jpeg())
        assert r["seq"] == 1 and not r["page_listening"], "no page has asked yet"
        item = look_now.wait(0, timeout=1)
        assert item["seq"] == 1 and item["source"] == "hotkey" and "image" not in item
        assert look_now.page_listening()

    def test_the_page_is_woken_the_moment_it_happens(self):
        got = {}
        t = threading.Thread(target=lambda: got.setdefault("item", look_now.wait(0, timeout=5)))
        t.start()
        time.sleep(0.2)
        started = time.time()
        look_now.request("wake", question="why?", image=jpeg())
        t.join(3)
        assert got["item"]["question"] == "why?" and time.time() - started < 1

    def test_the_image_is_used_once(self):
        look_now.request("hotkey", image=jpeg())
        assert look_now.image_for(1) == jpeg()
        assert look_now.image_for(1) is None

    def test_old_captures_expire(self):
        look_now.request("hotkey", image=jpeg(), now=time.time() - look_now.KEEP_SECONDS - 1)
        assert look_now.wait(0, timeout=0.1) is None

    def test_captures_fit_the_companion_limit(self):
        from agent import companion
        big = Image.new("RGB", (3840, 2160), "white")
        url = look_now.encode(big)
        assert companion.validate_screen_image(url), "a 4K screen must be scaled to fit"


class TestOnePageAnswers:
    """With the companion tab and the board both open, both pages poll. A
    press used to reach both: two answers, and the second one's capture was
    already used, so it showed an error."""

    def test_a_request_goes_to_one_page_only(self):
        look_now.request("hotkey", image=jpeg())
        first = look_now.wait(0, timeout=1)
        second = look_now.wait(0, timeout=0.2)
        assert first["seq"] == 1 and second is None

    def test_two_waiting_pages_get_one_each_press(self):
        got = []
        threads = [threading.Thread(target=lambda: got.append(look_now.wait(0, timeout=1.5))) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        look_now.request("hotkey", image=jpeg())
        for t in threads:
            t.join(3)
        assert sorted(i is not None for i in got) == [False, True]


class TestTheTalkHotkey:

    def test_talk_captures_nothing_and_says_what_it_is(self, monkeypatch):
        monkeypatch.setattr(look_now, "capture", lambda: pytest.fail("talk must not capture the screen"))
        look_now.request("hotkey", kind="talk")
        item = look_now.wait(0, timeout=1)
        assert item["kind"] == "talk" and look_now.image_for(item["seq"]) is None

    def test_look_items_say_so(self):
        look_now.request("hotkey", image=jpeg())
        assert look_now.wait(0, timeout=1)["kind"] == "look"

    def test_default_is_ctrl_alt_space(self):
        assert config.CELINE_TALK_HOTKEY == "<ctrl>+<alt>+<space>"


class TestStartBindsTheRealHotkeys:
    """start() through the REAL app.hotkey class, with only pynput faked.
    It imported a class that did not exist, and the error was caught and
    printed: Ctrl+Alt+C never bound on any machine, and no test ran start()."""

    @pytest.fixture
    def pynput(self, monkeypatch):
        import sys, types
        bound = {}
        class GlobalHotKeys:
            def __init__(self, bindings):
                bound.update(bindings)
            def start(self):
                pass
        keyboard = types.ModuleType("pynput.keyboard"); keyboard.GlobalHotKeys = GlobalHotKeys
        pkg = types.ModuleType("pynput"); pkg.keyboard = keyboard
        monkeypatch.setitem(sys.modules, "pynput", pkg)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", keyboard)
        return bound

    def test_both_hotkeys_bind_and_do_their_jobs(self, pynput, monkeypatch):
        monkeypatch.setattr(look_now, "capture", jpeg)
        started = look_now.start("<ctrl>+<alt>+c", None, talk_hotkey="<ctrl>+<alt>+<space>")
        assert started == ["look <ctrl>+<alt>+c", "talk <ctrl>+<alt>+<space>"]
        assert set(pynput) == {"<ctrl>+<alt>+c", "<ctrl>+<alt>+<space>"}
        pynput["<ctrl>+<alt>+<space>"]()
        pynput["<ctrl>+<alt>+c"]()
        kinds = [look_now.wait(0, timeout=1)["kind"], look_now.wait(0, timeout=1)["kind"]]
        assert kinds == ["talk", "look"]

    def test_an_empty_hotkey_is_off(self, pynput):
        assert look_now.start("", None, talk_hotkey="<ctrl>+<alt>+<space>") == ["talk <ctrl>+<alt>+<space>"]
        assert set(pynput) == {"<ctrl>+<alt>+<space>"}


class TestTheRoutes:

    @pytest.fixture
    def client(self, monkeypatch, test_db):
        from fastapi.testclient import TestClient
        from dashboard import companion as routes, server
        from tests.test_companion import FakeAgent
        fake = FakeAgent()
        monkeypatch.setattr(server, "_agent_ref", fake)
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        routes._active.clear(); routes._threads.clear()
        return TestClient(server.app), fake

    def test_the_page_gets_the_request_and_the_turn_gets_the_screen(self, client):
        c, fake = client
        look_now.request("hotkey", question="what's wrong here?", image=jpeg())
        data = c.get("/api/companion/look?after=0").json()
        assert data["item"]["question"] == "what's wrong here?" and "image" not in data["item"]
        r = c.post("/api/companion/chat", json={"message": "what's wrong here?", "mode": "discuss",
                                                "turn_id": "look-turn-aaaaaaaaaa1", "look_id": data["item"]["seq"]})
        assert r.status_code == 200
        (_msg, kwargs), = fake.calls
        assert kwargs["screen_image"] == jpeg(), "the capture the hotkey took, attached by the server"
        assert kwargs["screen_origin"] == "host"

    def test_a_capture_cannot_be_used_twice_or_guessed(self, client):
        c, fake = client
        look_now.request("hotkey", image=jpeg())
        ok = c.post("/api/companion/chat", json={"message": "look", "mode": "discuss",
                                                 "turn_id": "look-turn-aaaaaaaaaa2", "look_id": 1})
        assert ok.status_code == 200
        again = c.post("/api/companion/chat", json={"message": "look", "mode": "discuss",
                                                    "turn_id": "look-turn-aaaaaaaaaa3", "look_id": 1})
        assert again.status_code == 400 and "expired" in again.json()["detail"]
        bad = c.post("/api/companion/chat", json={"message": "look", "mode": "discuss",
                                                  "turn_id": "look-turn-aaaaaaaaaa4", "look_id": "1"})
        assert bad.status_code == 400


def test_the_model_is_told_whose_screen_it_is(monkeypatch, test_db):
    from types import SimpleNamespace
    from agent import core, schema, telemetry
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    schema.init_all(log=lambda *a: None)
    a = core.AgentCore()
    monkeypatch.setattr(a, "_all_tools", lambda: [])
    monkeypatch.setattr(a, "_maybe_autocreate_skill", lambda *args: None)
    monkeypatch.setattr(core._budget, "check", lambda: None)
    from agent import router
    monkeypatch.setattr(router, "route_model", lambda *args: ("test-model", 0))
    seen = {}

    def create(*args, **kwargs):
        seen["content"] = kwargs["messages"][-1]["content"]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")
    monkeypatch.setattr(telemetry, "create", create)
    a.run("look", channel_id="companion:31", companion_mode="discuss", screen_image=jpeg(), screen_origin="host")
    texts = " ".join(x.get("text", "") for x in seen["content"] if x["type"] == "text")
    assert "captured the moment they asked you to look" in texts
    assert "not the Apex host screen" not in texts


def test_the_launcher_turns_the_wake_phrase_on_unless_env_says_otherwise():
    spec = importlib.util.spec_from_file_location("run_apex_qwen_l", ROOT / "scripts" / "run_apex_qwen.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    assert launcher.apply_wake_default({}, dotenv_keys=set())["CELINE_WAKE_ENABLED"] == "true"
    assert "CELINE_WAKE_ENABLED" not in launcher.apply_wake_default({}, dotenv_keys={"CELINE_WAKE_ENABLED"}), \
        ".env must be able to turn it off"
    assert launcher.apply_wake_default({"CELINE_WAKE_ENABLED": "false"}, dotenv_keys=set())["CELINE_WAKE_ENABLED"] == "false"
