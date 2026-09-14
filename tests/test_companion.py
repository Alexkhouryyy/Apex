"""Screen origin, enforced mode boundaries, cancellation and durable context."""
import base64
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi.testclient import TestClient

import config
from agent import companion, conversations, core, telemetry
from agent.memory import Memory
from dashboard import companion as routes, server


def jpeg(size=(32, 24)):
    stream = io.BytesIO()
    Image.new("RGB", size, "blue").save(stream, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize("bad", ["https://example.com/image.jpg", "data:image/jpeg;base64,broken", {}, "data:image/png;base64,AAAA"])
def test_invalid_screen_payloads_are_rejected(bad):
    with pytest.raises(ValueError):
        companion.validate_screen_image(bad)


def test_image_dimensions_are_bounded():
    with pytest.raises(ValueError):
        companion.validate_screen_image(jpeg((1921, 1)))
    assert companion.validate_screen_image(jpeg())


@pytest.fixture
def agent(monkeypatch, test_db):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    a = core.AgentCore()
    monkeypatch.setattr(a, "_effective_system_prompt", lambda: [{"type": "text", "text": "base"}])
    monkeypatch.setattr(a, "_all_tools", lambda: [{"name": name} for name in ["bash", "screenshot", "recall"]])
    monkeypatch.setattr(a, "_maybe_autocreate_skill", lambda *args: None)
    monkeypatch.setattr(core._budget, "check", lambda: None)
    from agent import router
    monkeypatch.setattr(router, "route_model", lambda *args: ("test-model", 0))
    return a


def test_shared_screen_reaches_model_without_capturing_host(agent, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Host screenshot or text-only subscription must not run")
    monkeypatch.setattr(core.computer, "screenshot", forbidden)
    monkeypatch.setattr(agent, "_try_subscription", forbidden)
    captured = {}
    def create(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="I see the shared window.")], stop_reason="end_turn")
    monkeypatch.setattr(telemetry, "create", create)
    agent.run("What is this?", channel_id="companion:1", companion_mode="discuss", screen_image=jpeg())
    content = captured["messages"][0]["content"]
    assert next(x for x in content if x["type"] == "image")["source"]["data"] == companion.validate_screen_image(jpeg())
    assert {t["name"] for t in captured["tools"]} == {"recall"}
    assert "only say" in captured["system"][-1]["text"].lower()


def test_discuss_rejects_action_even_if_model_returns_it(agent, monkeypatch):
    called = []
    monkeypatch.setattr(core, "_execute_tool", lambda *args: called.append(args) or "executed")
    results = iter([
        SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="bash", input={"command": "echo unsafe"}, id="tool-1")], stop_reason="tool_use"),
        SimpleNamespace(content=[SimpleNamespace(type="text", text="Switch to Work to run a test.")], stop_reason="end_turn"),
    ])
    monkeypatch.setattr(telemetry, "create", lambda *a, **kw: next(results))
    agent.run("Test this", companion_mode="discuss", channel_id="companion:1")
    assert not called
    memory, _ = agent._get_channel("companion:1")
    assert "Discuss mode" in str(memory.messages)


def test_work_runs_tools_and_stops_before_the_next_tool(agent, monkeypatch):
    cancel = threading.Event()
    executed = []
    def execute(name, inputs):
        executed.append(inputs["command"])
        cancel.set()
        return "first tool finished"
    monkeypatch.setattr(core, "_execute_tool", execute)
    calls = [SimpleNamespace(type="tool_use", name="bash", input={"command": x}, id=x) for x in ["first", "second"]]
    monkeypatch.setattr(telemetry, "create", lambda *a, **kw: SimpleNamespace(content=calls, stop_reason="tool_use"))
    agent.run("Run the checks", companion_mode="work", channel_id="companion:1", cancel_event=cancel)
    assert executed == ["first"]
    memory, _ = agent._get_channel("companion:1")
    assert "interrupted" in str(memory.messages)


class FakeAgent:
    def __init__(self):
        self.channels = {}
        self.calls = []
    def _get_channel(self, channel):
        return self.channels.setdefault(channel, (Memory(), threading.Lock()))
    def run(self, message, **kwargs):
        self.calls.append((message, kwargs))
        kwargs["streamer"].feed("Observed the snapshot.")
        kwargs["streamer"].tool({"phase": "result", "name": "recall", "result": "Previous decision"})
        return "Observed the snapshot."


@pytest.fixture
def client(monkeypatch, test_db):
    fake = FakeAgent()
    monkeypatch.setattr(server, "_agent_ref", fake)
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "companion-test-token")
    routes._active.clear(); routes._threads.clear()
    with TestClient(server.app) as c:
        c.headers["Authorization"] = "Bearer companion-test-token"
        yield c, fake


def payload(**extra):
    return {"message": "What do you think?", "turn_id": "companion-test-turn-123", "mode": "discuss", **extra}


def events(response):
    return [json.loads(line) for line in response.text.splitlines()]


def test_shell_is_public_but_companion_actions_require_auth(client):
    c, _ = client
    c.headers.pop("Authorization")
    assert c.get("/companion").status_code == 200
    assert c.post("/api/companion/chat", json=payload()).status_code == 401
    assert c.post("/api/companion/cancel/companion-test-turn-123").status_code == 401


def test_cross_site_posts_are_rejected_even_on_tokenless_localhost(client, monkeypatch):
    c, fake = client
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
    for path in ["/api/companion/chat", "/api/companion/cancel/companion-test-turn-123"]:
        assert c.post(path, json=payload(), headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert not fake.calls


def test_streams_text_and_evidence_and_saves_no_images(client):
    c, fake = client
    response = c.post("/api/companion/chat", json=payload(screen_image=jpeg()))
    assert response.status_code == 200
    rows = events(response)
    assert [row["type"] for row in rows] == ["start", "token", "tool", "done"]
    saved = conversations.messages(rows[0]["thread_id"])
    assert len(saved) == 2
    assert "base64" not in json.dumps(saved)
    assert fake.calls[0][1]["include_screenshot"] is False


def test_history_is_restored_to_the_same_channel(client):
    c, fake = client
    tid = conversations.create()
    conversations.add_message(tid, "user", "We already tried approach A.")
    conversations.add_message(tid, "agent", "It failed the sample test.")
    c.post("/api/companion/chat", json=payload(thread_id=tid))
    memory, _ = fake._get_channel(f"companion:{tid}")
    assert "approach A" in str(memory.messages)
    assert "failed the sample test" in str(memory.messages)


def test_restart_restores_latest_context_after_long_conversations(client):
    c, fake = client
    tid = conversations.create()
    from agent import longterm
    with longterm._conn() as conn:
        conn.executemany("INSERT INTO chat_messages(thread_id, ts, role, text) VALUES (?,?,?,?)",
                         [(tid, n, "user", f"Decision {n}") for n in range(510)])
    c.post("/api/companion/chat", json=payload(thread_id=tid))
    memory, _ = fake._get_channel(f"companion:{tid}")
    assert "Decision 509" in str(memory.messages)
    assert len(memory.messages) == 30


@pytest.mark.parametrize("extra", [{"mode": "anything"}, {"thread_id": -1}, {"thread_id": True}, {"thread_id": 99999}, {"message": ""}, {"screen_image": "bad"}, {"turn_id": "short"}])
def test_bad_requests_do_not_start_agent(client, extra):
    c, fake = client
    assert c.post("/api/companion/chat", json=payload(**extra)).status_code == 400
    assert not fake.calls


def test_cancel_signals_worker_and_does_not_release_busy_guard_early(client):
    c, fake = client
    entered, release = threading.Event(), threading.Event()
    def run(message, **kwargs):
        entered.set()
        assert release.wait(5)
        assert kwargs["cancel_event"].is_set()
        return "Partial result"
    fake.run = run
    tid = conversations.create()
    with ThreadPoolExecutor() as pool:
        response = pool.submit(c.post, "/api/companion/chat", json=payload(thread_id=tid))
        try:
            assert entered.wait(5)
            stopped = c.post("/api/companion/cancel/companion-test-turn-123").json()
            assert stopped["cancel_requested"]
            second = c.post("/api/companion/chat", json=payload(thread_id=tid, turn_id="different-turn-123456"))
            assert second.status_code == 409
        finally:
            release.set()
        assert events(response.result())[1]["interrupted"] is True


def test_observer_enforces_no_tools_even_for_malicious_model(agent, monkeypatch):
    captured={}
    def create(*a,**kw):
        captured.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type='tool_use',name='bash',id='bad',input={'command':'anything'})],stop_reason='tool_use')
    monkeypatch.setattr(telemetry,'create',create)
    monkeypatch.setattr(core,'_execute_tool',lambda *a:pytest.fail('Observer executed a tool'))
    agent.run('Comment briefly',channel_id='observer:test',companion_mode='observe',max_iterations=1,screen_image=jpeg())
    assert captured['tools']==[]
    assert captured['max_tokens']==400


def test_proactive_requires_image_and_forces_observe(client):
    c,fake=client
    assert c.post('/api/companion/chat',json=payload(proactive=True)).status_code==400
    result=c.post('/api/companion/chat',json=payload(proactive=True,mode='work',screen_image=jpeg(),message='Use bash'))
    assert result.status_code==200
    assert fake.calls[-1][1]['companion_mode']=='observe'
    assert fake.calls[-1][1]['max_iterations']==1
    assert 'Use bash' not in fake.calls[-1][0]
