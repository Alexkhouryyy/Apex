"""Query complexity routing - send cheap queries to a small model, hard ones to the flagship.

A pure-heuristic classifier (no LLM call, ~microseconds) decides whether a turn is
"simple" (route to a Haiku-class model) or "complex" (keep the user's flagship model).
Conservative by design: when a turn is ambiguous it defaults to "complex" so answer
quality is never sacrificed to save a few cents, and core.py escalates a mis-routed
simple turn to the flagship if it starts doing real multi-step work.
"""
import re

import config
from agent.provider import provider_for

_COMPLEX_MIN_WORDS = 100
_SHORT_WORDS = 12

# Anything implying reasoning, generation, judgement, or code -> complex.
_COMPLEX_KEYWORDS = re.compile(
    r"\b(analy[sz]e|compare|contrast|write|draft|compose|essay|explain|why|"
    r"design|architect|plan|strateg|research|investigate|refactor|debug|"
    r"implement|optimi[sz]e|prove|derive|recommend|should\s+i|advice|advise|"
    r"opinion|decide|decision|pros\s+and\s+cons|trade-?offs?|evaluate|review|"
    r"summari[sz]e|brainstorm|code|program|function|algorithm|script)\b",
    re.I,
)

# Cheap lookups / commands -> simple.
_SIMPLE_KEYWORDS = re.compile(
    r"\b(remind\s+me|reminder|set\s+a\s+(timer|alarm)|schedule|what\s+time|"
    r"weather|convert|how\s+many|how\s+much|define|definition\s+of|who\s+is|"
    r"what'?s\s+the|translate|spell|currency|temperature)\b",
    re.I,
)

# Obvious code in the message -> complex.
_CODE_HINT = re.compile(r"```|def\s+\w+\s*\(|class\s+\w+|import\s+\w+|SELECT\s+|=>")


def classify_query(text: str, use_thinking: bool = False) -> str:
    """Return 'simple' or 'complex' for the given user message."""
    if use_thinking:
        return "complex"
    t = (text or "").strip()
    if not t:
        return "simple"
    words = len(t.split())

    # Strong complex signals win first.
    if _CODE_HINT.search(t) or _COMPLEX_KEYWORDS.search(t):
        return "complex"
    if words >= _COMPLEX_MIN_WORDS:
        return "complex"

    # Strong simple signals.
    if _SIMPLE_KEYWORDS.search(t):
        return "simple"
    if words <= _SHORT_WORDS:
        return "simple"

    # Medium length, no clear signal: be safe, keep the flagship.
    return "complex"


def route_model(user_text: str, current_model: str, use_thinking: bool = False):
    """Return (model_to_use, complexity|None).

    complexity is None when routing is disabled. Routing only ever *downgrades*
    a simple query to the small model; it never upgrades past the user's choice,
    and never crosses providers.
    """
    if not getattr(config, "SMART_ROUTING_ENABLED", False):
        return current_model, None
    complexity = classify_query(user_text, use_thinking)
    if complexity == "simple":
        simple = getattr(config, "ROUTING_SIMPLE_MODEL", "") or config.PROACTIVE_MODEL
        # The caller picks its client from the *user's* model and then sends the
        # routed name, so a cross-provider downgrade posts a Claude model name to
        # whatever endpoint the user actually selected. Ollama is the case that
        # bites: the two cost-saving features — local models and routing simple
        # queries to a cheap one — would break each other. There is nothing to
        # save by downgrading a model that already costs nothing.
        if provider_for(simple) != provider_for(current_model):
            return current_model, complexity
        return simple, complexity
    return current_model, complexity
