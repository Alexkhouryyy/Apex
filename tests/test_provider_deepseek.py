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
        ("deepseek-flash", "deepseek"),
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

    def test_flash_tool_roundtrip_without_anthropic_key(self, fake_deepseek, monkeypatch):
        from agent.core import AgentCore
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "AGENT_MODEL", "deepseek-flash")
        a = AgentCore()
        assert "Noted" in a.run("remember my name", include_screenshot=False)
        assert len(_FakeOpenAI.seen) >= 2
        for body in _FakeOpenAI.seen:
            assert body["model"] == "deepseek-flash"
            assert body["thinking"] == {"type": "disabled"}

    def test_missing_key_does_not_switch_model(self, fake_deepseek, monkeypatch):
        from agent.core import AgentCore
        a = AgentCore()
        before = a._model
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
        assert "DEEPSEEK_API_KEY" in a.set_model("deepseek-flash")
        assert a._model == before

    def test_flash_cannot_use_claude_subscription(self, fake_deepseek, monkeypatch):
        from agent.core import AgentCore
        monkeypatch.setattr(config, "AGENT_MODEL", "deepseek-flash")
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", True)
        a = AgentCore()
        assert a._try_subscription("hello", a.memory) is None

    def test_flash_visible_in_dashboard(self, fake_deepseek, monkeypatch):
        from dashboard import server
        monkeypatch.setattr(provider, "discover_all", lambda: {})
        models = server.list_models()["models"]
        flash = next(m for m in models if m["model"] == "deepseek-flash")
        assert flash["available"] is True
        assert flash["provider"] == "deepseek"

    def test_startup_reports_selected_provider_key(self, monkeypatch, capsys):
        import main
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
        monkeypatch.setattr("sys.argv", ["main.py", "--text", "--model", "deepseek-flash"])
        main.main()
        assert "needs DEEPSEEK_API_KEY" in capsys.readouterr().out

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


class TestBackgroundBrain:
    @pytest.mark.parametrize('site', sorted(__import__('agent.telemetry', fromlist=['_BACKGROUND_SITES'])._BACKGROUND_SITES))
    def test_automatic_calls_use_deepseek_not_stale_client(self, site, fake_deepseek, monkeypatch):
        from agent import telemetry
        from unittest.mock import Mock
        monkeypatch.setattr(config, 'BACKGROUND_MODEL', 'deepseek-flash')
        old_client = Mock()
        result = telemetry.create(old_client, call_site=site,
            model='claude-haiku-4-5', max_tokens=20,
            messages=[{'role': 'user', 'content': 'test background task'}],
            thinking={'type': 'enabled', 'budget_tokens': 1024})
        old_client.messages.create.assert_not_called()
        assert result.content[0].text == 'Noted.'
        body = _FakeOpenAI.seen[-1]
        assert body['model'] == 'deepseek-flash'
        assert body['thinking'] == {'type': 'disabled'}
        with fake_deepseek._conn() as c:
            logged = c.execute('SELECT model FROM usage_log WHERE call_site=?', (site,)).fetchone()
        assert logged[0] == 'deepseek-flash'

    def test_explicit_council_model_is_preserved(self, monkeypatch):
        from agent import telemetry
        from unittest.mock import Mock
        monkeypatch.setattr(config, 'BACKGROUND_MODEL', 'deepseek-flash')
        client = Mock()
        kwargs = {'model': 'claude-opus-5'}
        actual, request = telemetry._resolve_call(client, 'agent.constellation/planet', kwargs)
        assert actual is client
        assert request == kwargs

    def test_search_does_not_try_anthropic_in_deepseek_mode(self, monkeypatch):
        from tools import research
        from unittest.mock import Mock
        import ddgs
        monkeypatch.setattr(config, 'BACKGROUND_MODEL', 'deepseek-flash')
        old_search = Mock()
        monkeypatch.setattr(research, '_search_via_anthropic', old_search)
        search = Mock()
        search.return_value.text.return_value = [{'title': 'Result', 'href': 'https://example.com', 'body': 'Evidence'}]
        monkeypatch.setattr(ddgs, 'DDGS', search)
        assert research.search('test')[0]['url'] == 'https://example.com'
        old_search.assert_not_called()

    def test_bad_model_alias_is_normalized_before_tool_mode(self):
        result = provider._translate_kwargs({'model': 'deepseek-v4.1-flash', 'messages': []})
        assert result['model'] == 'deepseek-flash'
        assert result['extra_body']['thinking']['type'] == 'disabled'

    def test_deepseek_config_defaults_do_not_name_anthropic(self):
        import os
        import subprocess
        import sys
        env = dict(os.environ)
        for name in ('BACKGROUND_MODEL', 'PROACTIVE_MODEL', 'GUARDIAN_MODELS',
                     'CONSTELLATION_PLANET_MODEL', 'CONSTELLATION_MEMORY_MODEL',
                     'CONSTELLATION_SYNTH_MODEL', 'TIME_CAPSULE_MODEL'):
            env.pop(name, None)
        env['AGENT_MODEL'] = 'deepseek-v4.1-flash'
        env['PYTHON_DOTENV_DISABLED'] = '1'
        script = "import config; print(config.AGENT_MODEL, config.BACKGROUND_MODEL, config.PROACTIVE_MODEL, config.GUARDIAN_MODELS, config.CONSTELLATION_PLANET_MODEL, config.CONSTELLATION_MEMORY_MODEL, config.CONSTELLATION_SYNTH_MODEL, config.TIME_CAPSULE_MODEL)"
        result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, check=True)
        assert 'claude' not in result.stdout
        assert 'deepseek-v4.1-flash' not in result.stdout
        assert result.stdout.count('deepseek-flash') == 8

    def test_existing_key_migration_preserves_memory_and_secrets(self, tmp_path):
        import subprocess
        import sys
        import shutil
        from pathlib import Path
        from dotenv import dotenv_values
        scripts = tmp_path / 'scripts'
        scripts.mkdir()
        root = Path(__file__).resolve().parents[1]
        for name in ('setup_deepseek.py', 'set_env_key.py'):
            shutil.copy2(root / 'scripts' / name, scripts / name)
        original = 'DEEPSEEK_API_KEY=sk-test-private\nDB_PATH=C:/memory/old.db\nDASHBOARD_TOKEN=keep-this\nPROACTIVE_MODEL=claude-haiku-4-5\n'
        env = tmp_path / '.env'
        env.write_text(original)
        result = subprocess.run([sys.executable, str(scripts / 'setup_deepseek.py'), '--use-existing-key'], capture_output=True, text=True, check=True)
        values = dotenv_values(env)
        assert values['DEEPSEEK_API_KEY'] == 'sk-test-private'
        assert values['DB_PATH'] == 'C:/memory/old.db'
        assert values['DASHBOARD_TOKEN'] == 'keep-this'
        for name in ('AGENT_MODEL', 'BACKGROUND_MODEL', 'PROACTIVE_MODEL', 'SAFETY_REVIEW_MODEL', 'GUARDIAN_MODELS'):
            assert values[name] == 'deepseek-flash'
        assert 'sk-test-private' not in result.stdout + result.stderr
        assert next(tmp_path.glob('.env.before-deepseek-*')).read_text() == original
