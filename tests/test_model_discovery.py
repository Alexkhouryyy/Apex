"""Apex must be able to use a model released after this code was written.

KNOWN_MODELS is hand-written, and a hand-written list of other people's product
names goes stale. This one sat on `claude-opus-4-7` and `gpt-4o` well after both
were superseded, and because set_model() *gated* on it, a newer model could not
be selected at all without editing source.

The honest fix is not a longer list — guessing a model string produces a 404,
and guessing its price silently breaks the budget cap. It is to ask the
provider, which publishes what it actually serves.

Discovery fails soft everywhere: no key, no network, or an endpoint without
/models must read as "could not check", never as "no models exist" and never as
an exception inside a model picker.
"""
from __future__ import annotations

import pytest

import config
from agent import provider


@pytest.fixture(autouse=True)
def clear_cache():
    provider._DISCOVERED.clear()
    provider._DISCOVERED_AT.clear()
    yield
    provider._DISCOVERED.clear()
    provider._DISCOVERED_AT.clear()


class _Model:
    def __init__(self, mid):
        self.id = mid


def _fake_openai(ids):
    class _Models:
        @staticmethod
        def list():
            class _R:
                data = [_Model(i) for i in ids]
            return _R()

    class _Client:
        models = _Models()

        def __init__(self, *a, **k):
            pass
    return _Client


# --- the point of the whole thing --------------------------------------------

def test_a_model_released_later_becomes_usable(monkeypatch):
    """The regression. A model nobody hardcoded must still be selectable."""
    future = "gpt-5.6-luna"
    assert future not in provider.KNOWN_MODELS, "pick a name that is not curated"

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai([future, "gpt-5.1"]))

    assert provider.is_usable(future), (
        f"{future} is served by the provider but Apex refused it — the curated "
        f"list is acting as a gate again"
    )


def test_set_model_accepts_a_discovered_model(monkeypatch):
    future = "gpt-5.6-sol"
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai([future]))

    from agent.core import AgentCore
    core = AgentCore()
    msg = core.set_model(future)
    assert msg.startswith("Switched"), msg
    assert core._model == future


def test_a_model_nobody_serves_is_still_refused(monkeypatch):
    """Discovery must not turn set_model into a no-op that accepts typos."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai(["gpt-5.1"]))

    from agent.core import AgentCore
    core = AgentCore()
    before = core._model
    msg = core.set_model("gpt-5.6-tpyo")
    assert "Unknown model" in msg
    assert core._model == before, "a rejected model must not change state"
    assert "EXTRA_MODELS" in msg, "the error should name the escape hatch"


# --- failing soft ------------------------------------------------------------

def test_no_key_means_could_not_check_not_empty_truth(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    assert provider.discover("openai") == set()
    # ...and the curated list still works without a key.
    assert provider.is_usable("gpt-4o")


def test_network_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)

    def _boom(*a, **k):
        raise ConnectionError("no route to host")
    monkeypatch.setattr("openai.OpenAI", _boom)

    assert provider.discover("openai") == set()
    assert provider.is_usable("gpt-4o"), "curated models must survive an outage"


def test_unknown_provider_returns_empty(monkeypatch):
    assert provider.discover("not-a-provider") == set()


# --- shape of the ids --------------------------------------------------------

def test_ollama_ids_are_re_prefixed(monkeypatch):
    """The daemon reports bare names; Apex addresses them as ollama/<name>, and
    provider_for() keys off that prefix."""
    monkeypatch.setattr("openai.OpenAI", _fake_openai(["llama3", "mistral"]))
    found = provider.discover("ollama")
    assert found == {"ollama/llama3", "ollama/mistral"}
    assert all(provider.provider_for(m) == "ollama" for m in found)


def test_gemini_models_prefix_is_stripped(monkeypatch):
    """Gemini reports `models/gemini-3-pro`; a literal id with a slash would be
    routed to ollama by provider_for()."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k", raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai(["models/gemini-3-pro"]))
    found = provider.discover("gemini")
    assert found == {"gemini-3-pro"}
    assert provider.provider_for(next(iter(found))) == "gemini"


def test_results_are_cached(monkeypatch):
    calls = []

    def _counting(*a, **k):
        calls.append(1)
        return _fake_openai(["gpt-5.1"])()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("openai.OpenAI", _counting)

    provider.discover("openai")
    provider.discover("openai")
    assert len(calls) == 1, "discovery hit the network twice for one TTL window"


def test_force_bypasses_the_cache(monkeypatch):
    calls = []

    def _counting(*a, **k):
        calls.append(1)
        return _fake_openai(["gpt-5.1"])()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("openai.OpenAI", _counting)

    provider.discover("openai")
    provider.discover("openai", force=True)
    assert len(calls) == 2


# --- a discovered model must not silently disable the budget cap -------------

def test_unpriced_model_warns_loudly(capsys, monkeypatch):
    """$0 for an unknown model does not just mis-report — it takes the budget
    cap offline for that model, which is the one thing standing between Apex and
    an unbounded bill."""
    from agent import telemetry
    telemetry._warned_unpriced.clear()
    monkeypatch.setattr(config, "MODEL_PRICING", {}, raising=False)

    telemetry._pricing("gpt-5.6-max")
    out = capsys.readouterr().out
    assert "gpt-5.6-max" in out
    assert "budget cap" in out
    assert "MODEL_PRICING_JSON" in out, "the warning must say how to fix it"


def test_local_models_do_not_warn(capsys, monkeypatch):
    """ollama really is free; warning about it would train you to ignore this."""
    from agent import telemetry
    telemetry._warned_unpriced.clear()
    monkeypatch.setattr(config, "MODEL_PRICING", {}, raising=False)

    telemetry._pricing("ollama/llama3")
    assert "budget cap" not in capsys.readouterr().out


def test_warning_is_once_per_model(capsys, monkeypatch):
    """This sits on the hot path of every API call."""
    from agent import telemetry
    telemetry._warned_unpriced.clear()
    monkeypatch.setattr(config, "MODEL_PRICING", {}, raising=False)

    for _ in range(5):
        telemetry._pricing("gpt-5.6-max")
    assert capsys.readouterr().out.count("No price for") == 1


def test_priced_model_is_silent(capsys):
    from agent import telemetry
    telemetry._warned_unpriced.clear()
    p = telemetry._pricing("claude-opus-5")
    assert p["input"] == 5.0
    assert capsys.readouterr().out == ""


def test_env_pricing_is_applied(monkeypatch):
    """MODEL_PRICING_JSON lets a discovered model be priced without a code edit,
    which is what keeps the budget cap working for it."""
    import importlib
    monkeypatch.setenv("MODEL_PRICING_JSON",
                       '{"gpt-5.6-max": {"input": 7.5, "output": 30.0}}')
    cfg = importlib.reload(config)
    try:
        assert cfg.MODEL_PRICING["gpt-5.6-max"]["input"] == 7.5
        assert cfg.MODEL_PRICING["gpt-5.6-max"]["output"] == 30.0
        assert cfg.MODEL_PRICING["gpt-5.6-max"]["cache_read"] == 0.0
        assert cfg.MODEL_PRICING["claude-opus-5"]["input"] == 5.0, "built-ins survive"
    finally:
        monkeypatch.delenv("MODEL_PRICING_JSON", raising=False)
        importlib.reload(config)


def test_malformed_env_pricing_does_not_crash_boot(monkeypatch, capsys):
    import importlib
    monkeypatch.setenv("MODEL_PRICING_JSON", "{not json")
    try:
        cfg = importlib.reload(config)
        assert "claude-opus-5" in cfg.MODEL_PRICING, "built-ins must survive"
        assert "ignored" in capsys.readouterr().out
    finally:
        monkeypatch.delenv("MODEL_PRICING_JSON", raising=False)
        importlib.reload(config)
