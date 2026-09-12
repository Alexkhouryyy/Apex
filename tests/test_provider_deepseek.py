"""DeepSeek, and the adapter path every non-Anthropic provider shares.

Adding DeepSeek cost a branch and a base URL, because `OpenAIAdapter` already
existed. The interesting part was not the addition — it was discovering that
the path it joins had never been exercised.

Every `tools/smoke.py` run speaks the Anthropic protocol. So the adapter that
Gemini, Ollama and now DeepSeek all route through had never executed a single
tool call in any test, and "Apex is model-agnostic" was an architectural claim
with nothing behind it. `TestItCanActuallyDriveApex` is that claim, checked.
"""
import json
import socket
import sqlite3
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import config
from agent import provider


class TestRouting:
    @pytest.mark.parametrize("model,expected", [
        ("deepseek-chat", "deepseek"),
        ("deepseek-reasoner", "deepseek"),
        ("claude-opus-5", "anthropic"),
        ("gpt-5.1", "openai"),
        ("gemini-3-pro", "gemini"),
    ])
    def test_models_route_to_their_provider(self, model, expected):
        assert provider.provider_for(model) == expected

    def test_a_local_deepseek_still_goes_to_ollama(self):
        """`ollama/deepseek-r1` is a model on YOUR machine that happens to be
        named after DeepSeek. Sending it to DeepSeek's API would be a request
        over the network for something sitting on the laptop — and, for anyone
        running local models precisely to keep data local, a silent leak.

        What makes this safe is the prefix, not the order of the checks —
        "ollama/deepseek-r1" does not start with "deepseek". Worth saying
        plainly, because the first version of this test claimed the ordering was
        load-bearing, and reverting the order to prove it showed the test still
        passed. The property is still worth pinning; the explanation was wrong.
        """
        assert provider.provider_for("ollama/deepseek-r1") == "ollama"

    def test_it_is_in_the_known_models(self):
        assert {"deepseek-chat", "deepseek-reasoner"} <= provider.KNOWN_MODELS


class TestTheClient:
    def test_it_is_the_openai_adapter_pointed_at_deepseek(self, monkeypatch):
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-x", raising=False)
        monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "", raising=False)
        c = provider.get_client("deepseek-chat")
        assert type(c).__name__ == "OpenAIAdapter"
        assert str(c._oai.base_url).rstrip("/") == provider.DEEPSEEK_BASE_URL.rstrip("/")

    def test_the_base_url_can_be_overridden(self, monkeypatch):
        """For a gateway, a proxy, or the fake below — which is the only way
        this path gets tested without a paid key."""
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-x", raising=False)
        monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "http://127.0.0.1:9",
                            raising=False)
        c = provider.get_client("deepseek-chat")
        assert "127.0.0.1:9" in str(c._oai.base_url)

    def test_no_thinking_parameter_is_sent(self):
        """`thinking` is Anthropic-only and a 400 elsewhere. Callers must omit
        the key entirely, which is why None means omit rather than send null."""
        assert provider.thinking_params("deepseek-chat") is None
        assert provider.thinking_params("deepseek-chat", budget=4000) is None

    def test_sampling_parameters_are_allowed(self):
        assert provider.supports_sampling("deepseek-chat") is True


class TestPricing:
    def test_both_models_are_priced(self):
        """An unpriced model bills as $0 in telemetry, which silently
        understates spend — and the budget cap then protects nothing."""
        for m in ("deepseek-chat", "deepseek-reasoner"):
            p = config.MODEL_PRICING.get(m)
            assert p, f"{m} is unpriced; every call would record as free"
            assert p["input"] > 0 and p["output"] > 0

    def test_it_is_priced_below_the_anthropic_default(self):
        """The reason to add it at all. If this ever stops being true the
        rationale is gone and someone should notice."""
        assert (config.MODEL_PRICING["deepseek-chat"]["input"]
                < config.MODEL_PRICING["claude-opus-5"]["input"])


# ── the part that matters ────────────────────────────────────────────────────

class _FakeOpenAI(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint that asks for one tool, then answers."""
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or "{}")
        type(self).seen.append(body)
        already = any(m.get("role") == "tool" for m in body.get("messages", []))
        if body.get("tools") and not already:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "remember",
                             "arguments": json.dumps({"content": "User is Alex",
                                                      "kind": "fact",
                                                      "importance": 9})}}]}
            finish = "tool_calls"
        else:
            msg, finish = {"role": "assistant", "content": "Noted."}, "stop"
        out = json.dumps({
            "id": "chatcmpl-x", "object": "chat.completion", "created": 0,
            "model": body.get("model", "deepseek-chat"),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def fake_deepseek(tmp_path, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _FakeOpenAI.seen = []
    srv = HTTPServer(("127.0.0.1", port), _FakeOpenAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from agent import longterm, schema
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "brain.db"))
    schema.init_all(log=lambda *a: None)
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-fake", raising=False)
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", f"http://127.0.0.1:{port}",
                        raising=False)
    try:
        yield longterm
    finally:
        srv.shutdown()
        srv.server_close()


class TestItCanActuallyDriveApex:
    """Model-agnostic was an architectural claim with nothing behind it: every
    smoke run speaks the Anthropic protocol, so the adapter shared by Gemini,
    Ollama and DeepSeek had never executed a tool call in any test.

    A model that is excellent at chat can be unusable as an agent. This is the
    difference, checked rather than assumed.
    """

    def test_a_tool_call_reaches_apex_and_changes_the_world(self, fake_deepseek):
        from agent.core import AgentCore
        a = AgentCore()
        a.set_model("deepseek-chat")
        a.run("remember that my name is Alex", include_screenshot=False)

        with fake_deepseek._conn() as c:
            memories = [r[0] for r in c.execute("SELECT content FROM memories")]
            events = c.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0]
        assert "User is Alex" in memories, (
            "the tool call did not take effect — a non-Anthropic model cannot "
            "drive Apex, whatever the routing says")
        assert events >= 1

    def test_the_whole_toolbox_is_offered(self, fake_deepseek):
        """Not a reduced set. If the adapter dropped tools, Apex would look
        mysteriously less capable on one provider than another."""
        from agent.core import AgentCore
        a = AgentCore()
        a.set_model("deepseek-chat")
        a.run("hello", include_screenshot=False)
        first = _FakeOpenAI.seen[0]
        assert len(first.get("tools", [])) > 50, \
            f"only {len(first.get('tools', []))} tools reached the provider"

    def test_it_sends_no_anthropic_only_parameters(self, fake_deepseek):
        """`thinking` is a 400 on an OpenAI-compatible endpoint, not a warning."""
        from agent.core import AgentCore
        a = AgentCore()
        a.set_model("deepseek-chat")
        a.run("hello", include_screenshot=False)
        for body in _FakeOpenAI.seen:
            assert "thinking" not in body, "sent `thinking` to a non-Anthropic API"

    def test_the_conversation_completes_after_the_tool(self, fake_deepseek):
        """Two round trips: the tool call, then the answer. A loop that stopped
        after the tool would leave the user with no reply."""
        from agent.core import AgentCore
        a = AgentCore()
        a.set_model("deepseek-chat")
        out = a.run("remember that my name is Alex", include_screenshot=False)
        assert "Noted" in str(out)
        assert len(_FakeOpenAI.seen) >= 2


class TestAMissingKeySaysWhichKey:
    """The OpenAI SDK raises "Missing credentials ... set the OPENAI_API_KEY
    environment variable". For a DeepSeek or Gemini model that names a variable
    which would not help — an error that sends someone to set the wrong thing
    is worse than one that says nothing.

    Found because adding two models to KNOWN_MODELS made a pre-existing test
    fail with exactly that message.
    """

    @pytest.mark.parametrize("model,key", [
        ("deepseek-chat", "DEEPSEEK_API_KEY"),
        ("gemini-3-pro", "GEMINI_API_KEY"),
        ("gpt-5.1", "OPENAI_API_KEY"),
        ("claude-opus-5", "ANTHROPIC_API_KEY"),
    ])
    def test_it_names_the_variable_that_would_actually_help(self, model, key,
                                                            monkeypatch):
        monkeypatch.setattr(config, key, "", raising=False)
        with pytest.raises(provider.MissingProviderKey) as e:
            provider.get_client(model)
        assert key in str(e.value)

    def test_the_message_carries_the_command_to_fix_it(self, monkeypatch):
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
        with pytest.raises(provider.MissingProviderKey) as e:
            provider.get_client("deepseek-chat")
        assert "set_env_key.py DEEPSEEK_API_KEY" in str(e.value)

    def test_ollama_needs_no_key(self, monkeypatch):
        """A local daemon. Demanding a key would make local models unusable for
        the people most likely to want them."""
        monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://x/v1", raising=False)
        assert type(provider.get_client("ollama/llama3.2")).__name__ == "OpenAIAdapter"

    def test_every_provider_has_a_key_name_recorded(self):
        """So the error message, discovery and the test fixture cannot drift
        apart the next time a provider is added."""
        for m in ("claude-opus-5", "gpt-5.1", "gemini-3-pro", "deepseek-chat",
                  "ollama/llama3.2"):
            assert provider.provider_for(m) in provider.PROVIDER_KEY_NAMES
