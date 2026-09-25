"""Celine: in her voice, Apex answers as her — by name and by personality."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from agent import celine, core, telemetry


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAULT_PATH", str(tmp_path), raising=False)
    return tmp_path


class TestWhoSpeaks:

    def test_her_profile_in_the_local_voice_is_celine(self, monkeypatch):
        monkeypatch.setattr(config, "VOICEBOX_PROFILE", "", raising=False)
        assert celine.wanted("voicebox", "celine")
        assert celine.wanted("voicebox", "CELINE")

    def test_the_launcher_default_counts(self, monkeypatch):
        """Start-Apex-Celine sets VOICEBOX_PROFILE=celine; 'Apex default voice'
        in the picker then IS Celine."""
        monkeypatch.setattr(config, "VOICEBOX_PROFILE", "celine", raising=False)
        assert celine.wanted("voicebox", "")

    @pytest.mark.parametrize("voice,profile", [("browser", "celine"), ("openai", "celine"),
                                               ("voicebox", "p-ryan"), (None, None)])
    def test_other_voices_are_not_celine(self, monkeypatch, voice, profile):
        monkeypatch.setattr(config, "VOICEBOX_PROFILE", "", raising=False)
        assert not celine.wanted(voice, profile)


class TestHerPersona:

    def test_she_knows_her_name(self, vault):
        block = celine.persona_block()
        assert "Your name is Celine" in block and "a version of Apex" in block
        assert 'never call the user "sir"' in block

    def test_her_personality_is_the_users_note_when_there_is_one(self, vault):
        (vault / "Celine.md").write_text("She loves bad puns and hates corporate speak.", encoding="utf-8")
        block = celine.persona_block()
        assert "bad puns" in block and "Warm, quick and direct" not in block
        assert "Your name is Celine" in block, "the note sets personality, not identity"

    def test_an_empty_note_falls_back_to_the_default(self, vault):
        (vault / "Celine.md").write_text("   \n", encoding="utf-8")
        assert "Warm, quick and direct" in celine.persona_block()


class TestTheTurn:

    @pytest.fixture
    def agent(self, monkeypatch, test_db, vault):
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
        from agent import schema
        schema.init_all(log=lambda *a: None)       # the real system prompt reads goals
        a = core.AgentCore()
        monkeypatch.setattr(a, "_all_tools", lambda: [{"name": "recall"}])
        monkeypatch.setattr(a, "_maybe_autocreate_skill", lambda *args: None)
        monkeypatch.setattr(core._budget, "check", lambda: None)
        from agent import router
        monkeypatch.setattr(router, "route_model", lambda *args: ("test-model", 0))
        seen = {}

        def create(*args, **kwargs):
            seen["system"] = "\n".join(b["text"] for b in kwargs["system"])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Hi, I'm Celine.")],
                                   stop_reason="end_turn")
        monkeypatch.setattr(telemetry, "create", create)
        return a, seen

    def test_in_her_voice_the_system_prompt_is_hers(self, agent):
        a, seen = agent
        a.run("what's your name?", channel_id="companion:9", companion_mode="discuss", persona="celine")
        assert "Your name is Celine" in seen["system"]
        assert "You are Celine, the user's screen companion" in seen["system"]
        assert "## PERSONA — JARVIS" not in seen["system"], "the butler persona must not ride along"

    def test_without_her_voice_nothing_changes(self, agent):
        a, seen = agent
        a.run("hello", channel_id="companion:9", companion_mode="discuss")
        assert "Your name is Celine" not in seen["system"]
        assert "You are Apex, the user's screen companion" in seen["system"]


class TestTheRoute:

    def test_her_voice_selection_reaches_the_turn(self, monkeypatch, test_db):
        from fastapi.testclient import TestClient
        from dashboard import companion as routes, server
        from tests.test_companion import FakeAgent
        fake = FakeAgent()
        monkeypatch.setattr(server, "_agent_ref", fake)
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        monkeypatch.setattr(config, "VOICEBOX_PROFILE", "", raising=False)
        routes._active.clear(); routes._threads.clear()
        c = TestClient(server.app)
        base = {"message": "hi", "mode": "discuss"}
        c.post("/api/companion/chat", json={**base, "turn_id": "celine-turn-aaaaaaaa1",
                                             "voice": "voicebox", "voice_profile": "celine"})
        c.post("/api/companion/chat", json={**base, "turn_id": "celine-turn-aaaaaaaa2",
                                             "voice": "browser", "voice_profile": ""})
        assert [kw.get("persona") for _m, kw in fake.calls] == ["celine", None]
        bad = c.post("/api/companion/chat", json={**base, "turn_id": "celine-turn-aaaaaaaa3", "voice": 5})
        assert bad.status_code == 400


class TestSheRemembers:
    """Companion conversations each start with an empty channel memory; long-
    term memory used to reach them only if the model thought to call recall."""

    @pytest.fixture
    def agent(self, monkeypatch, test_db, vault):
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
        from agent import schema
        schema.init_all(log=lambda *a: None)
        a = core.AgentCore()
        monkeypatch.setattr(a, "_all_tools", lambda: [{"name": n} for n in ("recall", "remember", "bash")])
        monkeypatch.setattr(a, "_maybe_autocreate_skill", lambda *args: None)
        monkeypatch.setattr(core._budget, "check", lambda: None)
        from agent import router
        monkeypatch.setattr(router, "route_model", lambda *args: ("test-model", 0))
        seen = []

        def create(*args, **kwargs):
            seen.append({"system": "\n".join(b["text"] for b in kwargs["system"]),
                         "tools": {t["name"] for t in kwargs.get("tools", [])}})
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")
        monkeypatch.setattr(telemetry, "create", create)
        return a, seen

    def test_her_turn_carries_long_term_memory(self, agent):
        from agent import longterm
        longterm.remember("The user's dog is called Rocket.", kind="fact", importance=8)
        a, seen = agent
        a.run("what's my dog called?", channel_id="companion:21", companion_mode="discuss", persona="celine")
        assert "Rocket" in seen[-1]["system"]

    def test_something_saved_mid_conversation_is_known_next_turn(self, agent):
        from agent import longterm
        a, seen = agent
        a.run("hi", channel_id="companion:22", companion_mode="discuss")
        assert "Lisbon" not in seen[-1]["system"]
        longterm.remember("The user is moving to Lisbon in March.", kind="fact", importance=7)
        a.run("remind me", channel_id="companion:22", companion_mode="discuss")
        assert "Lisbon" in seen[-1]["system"]

    def test_she_can_save_memories_even_in_discuss(self, agent):
        a, seen = agent
        a.run("remember I like tea", channel_id="companion:23", companion_mode="discuss")
        assert seen[-1]["tools"] == {"recall", "remember"}, "bash must stay out of Discuss"

    def test_the_proactive_check_in_gets_no_memory_dump(self, agent):
        from agent import longterm
        longterm.remember("A private fact.", kind="fact", importance=9)
        a, seen = agent
        a.run("x", channel_id="companion:24", companion_mode="observe")
        assert "A private fact." not in seen[-1]["system"]
