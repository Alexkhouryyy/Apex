"""Every model in KNOWN_MODELS must reach the provider it names.

`AgentCore.client` re-implemented `provider.get_client()` and had drifted: it
handled anthropic, openai and gemini, but not ollama. `set_model("ollama/llama3")`
returned "Switched to ollama/llama3" — a success message — and the turn then
sent that model name to api.anthropic.com.

That is the worst-shaped version of this bug. Ollama is the zero-cost local
provider; it existed so Apex could run without spending anything, and it was
the one silently routed to the metered API. Nothing surfaced it because the
failure is a 404 from a provider that was never supposed to see the request.

The test is written over KNOWN_MODELS rather than over a list of providers, so
adding a provider without teaching the client about it fails here rather than
in production.
"""
from __future__ import annotations

import pytest

import config
from agent.provider import KNOWN_MODELS, provider_for


@pytest.fixture
def core(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-openai-test", raising=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "sk-gemini-test", raising=False)
    from agent.core import AgentCore
    return AgentCore()


def _endpoint(client) -> str:
    """Where would a request from this client actually go?"""
    inner = getattr(client, "_oai", None)
    return str(getattr(inner if inner is not None else client, "base_url", ""))


@pytest.mark.parametrize("model", sorted(KNOWN_MODELS))
def test_model_reaches_its_own_provider(core, model):
    core.set_model(model)
    assert core._model == model, f"set_model({model}) did not take"
    endpoint = _endpoint(core.client)
    if provider_for(model) == "anthropic":
        assert "anthropic.com" in endpoint
    else:
        assert "anthropic.com" not in endpoint, (
            f"{model} is a {provider_for(model)} model but its client points at "
            f"{endpoint} — the request would go to Anthropic under a model name "
            f"Anthropic does not have"
        )


@pytest.mark.parametrize("model", sorted(m for m in KNOWN_MODELS if m.startswith("ollama/")))
def test_ollama_models_reach_the_local_daemon(core, model):
    """The specific regression: local models must stay local, and cost nothing."""
    core.set_model(model)
    assert config.OLLAMA_BASE_URL.rstrip("/") in _endpoint(core.client).rstrip("/")


def test_one_adapter_is_reused_per_provider(core):
    """Two ollama models share an adapter — the model name travels per call, so
    building a client per model would be pure waste."""
    core.set_model("ollama/llama3")
    first = core.client
    core.set_model("ollama/mistral")
    assert core.client is first


def test_client_setter_still_injects_a_mock(core):
    """Tests across the suite override the client directly. Anthropic must keep
    being read off self.anthropic or that stops working."""
    sentinel = object()
    core.set_model("claude-opus-4-7")
    core.client = sentinel
    assert core.client is sentinel


def test_switching_back_to_anthropic_drops_the_adapter(core):
    core.set_model("ollama/llama3")
    assert "anthropic.com" not in _endpoint(core.client)
    core.set_model("claude-opus-4-7")
    assert core.client is core.anthropic


# --- the same mismatch, one layer up -----------------------------------------
# AgentCore picks its client from `self._model` but sends `route_model`'s answer.
# If routing may cross providers, those two disagree and the request goes to the
# wrong endpoint under a name it does not recognise.

def test_routing_never_crosses_providers(monkeypatch):
    """Enabling smart routing must not break local models.

    Both features exist to save money — routing sends simple queries to a cheap
    model, ollama runs free and local. Together they used to cancel out: a
    simple query on ollama/llama3 routed to claude-haiku, which AgentCore then
    posted to localhost:11434.
    """
    from agent import router
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "ROUTING_SIMPLE_MODEL", "claude-haiku-4-5-20251001",
                        raising=False)
    monkeypatch.setattr(router, "classify_query", lambda *a, **k: "simple")

    chosen, complexity = router.route_model("hi", "ollama/llama3")
    assert complexity == "simple", "the query should still be classified"
    assert provider_for(chosen) == "ollama", (
        f"a simple query on ollama/llama3 routed to {chosen} — AgentCore would "
        f"post that name to the Ollama daemon"
    )


def test_routing_still_downgrades_within_a_provider(monkeypatch):
    """The guard must not disable routing for the case it was built for."""
    from agent import router
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "ROUTING_SIMPLE_MODEL", "claude-haiku-4-5-20251001",
                        raising=False)
    monkeypatch.setattr(router, "classify_query", lambda *a, **k: "simple")

    chosen, _ = router.route_model("hi", "claude-opus-4-7")
    assert chosen == "claude-haiku-4-5-20251001"


def test_routed_model_and_client_always_agree(monkeypatch, core):
    """The invariant behind both tests, asserted directly."""
    from agent import router
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", True, raising=False)
    monkeypatch.setattr(router, "classify_query", lambda *a, **k: "simple")
    for model in sorted(KNOWN_MODELS):
        core.set_model(model)
        routed, _ = router.route_model("hi", core._model)
        assert provider_for(routed) == provider_for(core._model), (
            f"{model} -> routed to {routed}: client is built for "
            f"{provider_for(model)} but the request names a "
            f"{provider_for(routed)} model"
        )
