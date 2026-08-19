"""Request parameters that a model no longer accepts must not be sent.

Claude 4.7 removed two request parameters outright, and both are *hard* errors
rather than ignored fields:

    thinking={"type": "enabled", "budget_tokens": N}  -> 400
    temperature / top_p / top_k                       -> 400

So upgrading the default model from `claude-opus-4-7` to `claude-opus-5` is not
a find-and-replace. Swapping the ID alone would have left two live call sites
sending parameters the new default rejects:

  * `agent/core.py` set the old thinking shape whenever `--think` was on, so
    every extended-thinking turn would 400.
  * the best-of-n rerank path set `temperature=1.0`, so reranking would 400 on
    exactly the models it matters most for.
  * `agent/reflection.py` sent the old thinking shape from the unattended
    consolidation heartbeat, where a 400 fails silently and forever.

These tests are written over KNOWN_MODELS so a model added later without a
capability decision fails here rather than at runtime.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

import config
from agent import provider
from agent.provider import KNOWN_MODELS, provider_for

# Models that removed budget_tokens and sampling. Spelled out independently of
# the module's own set so a typo there cannot make these tests vacuous.
ADAPTIVE_ONLY = {
    "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
    "claude-opus-4-8", "claude-opus-4-7",
}


@pytest.mark.parametrize("model", sorted(ADAPTIVE_ONLY))
def test_no_budget_tokens_on_models_that_reject_it(model):
    t = provider.thinking_params(model, budget=8000)
    assert t == {"type": "adaptive"}, f"{model} would receive {t}"
    assert "budget_tokens" not in (t or {})


@pytest.mark.parametrize("model", sorted(ADAPTIVE_ONLY))
def test_no_sampling_on_models_that_reject_it(model):
    assert provider.supports_sampling(model) is False


def test_older_models_still_get_a_budget():
    """The fix must not silently disable thinking on models that still need the
    old shape."""
    t = provider.thinking_params("claude-haiku-4-5-20251001", budget=8000)
    assert t == {"type": "enabled", "budget_tokens": 8000}
    assert provider.supports_sampling("claude-haiku-4-5-20251001") is True


def test_46_takes_adaptive_thinking_but_keeps_sampling():
    for m in ("claude-opus-4-6", "claude-sonnet-4-6"):
        assert provider.thinking_params(m, 8000) == {"type": "adaptive"}
        assert provider.supports_sampling(m) is True


def test_non_anthropic_models_get_no_thinking_param():
    for m in ("gpt-5.1", "gemini-3-pro", "ollama/llama3"):
        assert provider.thinking_params(m, 8000) is None
        assert provider.supports_sampling(m) is True


def test_thinking_omitted_rather_than_none():
    """None means 'omit the key'. Passing thinking=None is a different request
    from not passing thinking, so callers must branch — this pins the contract
    the call sites rely on."""
    assert provider.thinking_params("gpt-5.1") is None
    assert provider.thinking_params("claude-haiku-4-5") is None  # no budget given


# --- the defaults actually shipped -------------------------------------------

def test_default_model_is_current():
    assert config.AGENT_MODEL == "claude-opus-5"


def test_every_configured_model_is_known():
    """A default nobody can select is a boot-time failure waiting to happen."""
    for name in ("AGENT_MODEL", "PROACTIVE_MODEL", "ROUTING_SIMPLE_MODEL",
                 "CONSTELLATION_PLANET_MODEL", "TIME_CAPSULE_MODEL"):
        value = getattr(config, name)
        assert value in KNOWN_MODELS, f"config.{name} = {value!r} is not selectable"
    for m in config.GUARDIAN_MODELS:
        assert m in KNOWN_MODELS, f"GUARDIAN_MODELS entry {m!r} is not selectable"


def test_council_roster_is_known_and_one_per_provider():
    from agent import council
    seen = set()
    for model, _label in council._ROSTER:
        assert model in KNOWN_MODELS, f"council roster has unknown model {model!r}"
        p = provider_for(model)
        assert p not in seen, f"two {p} models in the council roster"
        seen.add(p)
    assert council._CHAIR in KNOWN_MODELS


@pytest.mark.parametrize("model", sorted(m for m in KNOWN_MODELS
                                         if not m.startswith("ollama/")))
def test_every_paid_model_has_a_price(model):
    """An unpriced model silently bills as $0, which makes the budget cap — the
    thing standing between Apex and an unbounded bill — quietly stop working."""
    assert model in config.MODEL_PRICING, f"{model} has no entry in MODEL_PRICING"


def test_opus_pricing_is_not_the_old_inflated_figure():
    """Opus was listed at $15/$75, 3x its real rate, so every cost estimate and
    budget reading Apex produced was inflated."""
    assert config.MODEL_PRICING["claude-opus-5"]["input"] == 5.0
    assert config.MODEL_PRICING["claude-opus-5"]["output"] == 25.0


def test_extra_models_env_extends_the_list(monkeypatch):
    """The list must not be the only way to reach a new model — that is how it
    went stale in the first place."""
    import importlib
    monkeypatch.setenv("EXTRA_MODELS", "gpt-99,some-future-model")
    mod = importlib.reload(provider)
    try:
        assert "gpt-99" in mod.KNOWN_MODELS
        assert "some-future-model" in mod.KNOWN_MODELS
    finally:
        monkeypatch.delenv("EXTRA_MODELS", raising=False)
        importlib.reload(provider)


# --- no call site may hardcode the removed parameters ------------------------

REPO = pathlib.Path(__file__).resolve().parent.parent


def _sources():
    for d in ("agent", "tools", "dashboard", "app"):
        yield from (REPO / d).rglob("*.py")
    yield REPO / "main.py"


def test_no_source_hardcodes_budget_tokens():
    """Every thinking parameter must come from provider.thinking_params(), which
    knows what the model accepts. A literal is how this broke."""
    offenders = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        # thinking_params() is the one place allowed to spell it — it is the
        # function that decides. Exempt that body only, not the whole file, so a
        # stray literal elsewhere in provider.py still fails.
        # thinking_params() is the one place allowed to *build* the parameter;
        # the smoke check is the one place allowed to *detect* it. Both are
        # exempted by function, not by file, so a stray literal elsewhere in
        # either module still fails.
        allowed = {"thinking_params", "no_removed_parameters_are_sent"}
        exempt = {
            id(n) for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef) and fn.name in allowed
            for n in ast.walk(fn)
        }
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and node.value == "budget_tokens"
                    and id(node) not in exempt):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, (
        "budget_tokens is written literally at " + ", ".join(offenders) +
        " — it returns a 400 on Claude 4.7+; use provider.thinking_params()"
    )
