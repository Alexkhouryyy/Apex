"""Multi-provider LLM adapter.

Makes an OpenAI client look like an Anthropic client so AgentCore can
swap providers by changing self._model without touching any other code.
"""
from __future__ import annotations
import json
import os
from typing import Any


# Google's OpenAI-compatible endpoint — lets the OpenAI SDK talk to Gemini.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def provider_for(model: str) -> str:
    """Return 'anthropic', 'openai', 'gemini', or 'ollama' based on model name."""
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("ollama/"):
        return "ollama"
    return "openai"


KNOWN_MODELS = {
    # Anthropic — current generation. Model IDs are complete as written; never
    # append a date suffix (`claude-sonnet-5`, not `claude-sonnet-5-2026...`).
    "claude-opus-5",                       # default: 1M context, $5/$25
    "claude-fable-5",                      # most capable, $10/$50
    "claude-sonnet-5",
    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",           # legacy alias, kept so old .env files boot
    # OpenAI
    "gpt-5.1", "gpt-5", "gpt-5-mini",
    "gpt-4o", "gpt-4o-mini",
    "gpt-4-turbo",
    "o3", "o3-mini", "o4-mini",
    # Google Gemini
    "gemini-3-pro", "gemini-3-flash",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-2.0-flash",
    # Ollama local — any model pulled with `ollama pull <name>`; use ollama/ prefix.
    # These are the most common; any other pulled model works the same way.
    "ollama/llama3.2", "ollama/llama3.1", "ollama/llama3",
    "ollama/mistral", "ollama/mistral-nemo",
    "ollama/qwen2.5", "ollama/qwen2.5-coder",
    "ollama/phi4", "ollama/gemma3",
    "ollama/deepseek-r1",
}

# Providers ship models faster than this file gets edited, and being unable to
# use one until someone patches a set literal is a bad trade. EXTRA_MODELS is the
# escape hatch: `EXTRA_MODELS=gpt-6,gemini-4-pro` in .env and they are selectable
# immediately. Anything unpriced bills as $0 in telemetry, which is the honest
# failure — see config.MODEL_PRICING.
KNOWN_MODELS |= {
    m.strip() for m in os.getenv("EXTRA_MODELS", "").split(",") if m.strip()
}


# ── Per-model request capabilities ────────────────────────────────────────────
# Claude 4.7 and later removed two request parameters outright. Sending either is
# a 400, not a warning, so this has to be decided per model rather than globally:
#
#   thinking={"type": "enabled", "budget_tokens": N}  ->  400
#   temperature / top_p / top_k                       ->  400
#
# Adaptive thinking replaces the fixed budget: the model decides how much to
# think. Depth is steered with output_config.effort instead of a token count.

_ADAPTIVE_ONLY = {
    "claude-fable-5", "claude-opus-5", "claude-sonnet-5",
    "claude-opus-4-8", "claude-opus-4-7",
}
# 4.6 accepts adaptive thinking too, but still allows sampling parameters.
_ADAPTIVE_OK = _ADAPTIVE_ONLY | {"claude-opus-4-6", "claude-sonnet-4-6"}


def thinking_params(model: str, budget: int | None = None) -> dict | None:
    """The `thinking` value this model accepts, or None if it takes none.

    Callers must omit the key entirely when this returns None — passing
    `thinking=None` is not the same as not passing it.
    """
    if provider_for(model) != "anthropic":
        return None
    if model in _ADAPTIVE_OK:
        return {"type": "adaptive"}
    if budget:
        return {"type": "enabled", "budget_tokens": int(budget)}
    return None


def supports_sampling(model: str) -> bool:
    """Whether temperature/top_p/top_k may be sent. Removed on Claude 4.7+."""
    if provider_for(model) != "anthropic":
        return True
    return model not in _ADAPTIVE_ONLY


# ── Anthropic-compatible response objects ─────────────────────────────────────

class _Usage:
    __slots__ = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

    def __init__(self, inp: int, out: int):
        self.input_tokens = inp
        self.output_tokens = out
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _TextBlock(dict):
    """Dict-subclass so it works both as an object (block.text) and a dict (block.get('text'))."""

    def __init__(self, text: str):
        super().__init__(type="text", text=text)

    @property
    def type(self) -> str:
        return self["type"]

    @property
    def text(self) -> str:
        return self["text"]


class _ToolUseBlock(dict):
    """Dict-subclass so it works both as an object (block.name) and a dict (block.get('name'))."""

    def __init__(self, id_: str, name: str, input_: dict):
        super().__init__(type="tool_use", id=id_, name=name, input=input_)

    @property
    def type(self) -> str:
        return self["type"]

    @property
    def id(self) -> str:
        return self["id"]

    @property
    def name(self) -> str:
        return self["name"]

    @property
    def input(self) -> dict:
        return self["input"]


class _FakeMessage:
    __slots__ = ("content", "stop_reason", "usage")

    def __init__(self, content: list, stop_reason: str, usage: _Usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _TextDelta:
    __slots__ = ("type", "text")

    def __init__(self, text: str):
        self.type = "text_delta"
        self.text = text


class _Event:
    __slots__ = ("type", "delta")

    def __init__(self, text: str):
        self.type = "content_block_delta"
        self.delta = _TextDelta(text)


# ── Format translation helpers ────────────────────────────────────────────────

def _system_str(system) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = [
            b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
            for b in system
            if (isinstance(b, dict) and b.get("type") == "text")
            or (not isinstance(b, dict) and getattr(b, "type", "") == "text")
        ]
        return "\n\n".join(p for p in parts if p)
    return ""


def _translate_tools(tools: list) -> list:
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        schema = {k: v for k, v in t.get("input_schema", {}).items() if k != "cache_control"}
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": schema,
            },
        })
    return out


def _bt(b) -> str:
    return b.get("type", "") if isinstance(b, dict) else getattr(b, "type", "")


def _bget(b, key: str, default=None):
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def _translate_messages(messages: list) -> list:
    """Convert Anthropic-format message list to OpenAI-format."""
    result: list[dict] = []

    for msg in messages:
        role = _bget(msg, "role", "")
        content = _bget(msg, "content", [])

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            continue

        if role == "user":
            tool_results = [b for b in content if _bt(b) == "tool_result"]
            other = [b for b in content if _bt(b) != "tool_result"]

            # Tool results become role=tool messages
            for tr in tool_results:
                tr_content = _bget(tr, "content", "")
                if isinstance(tr_content, list):
                    tr_content = " ".join(
                        _bget(c, "text", "") for c in tr_content if _bt(c) == "text"
                    )
                result.append({
                    "role": "tool",
                    "tool_call_id": _bget(tr, "tool_use_id", ""),
                    "content": str(tr_content),
                })

            if other:
                parts = []
                for b in other:
                    bt = _bt(b)
                    if bt == "text":
                        parts.append({"type": "text", "text": _bget(b, "text", "")})
                    elif bt == "image":
                        src = _bget(b, "source", {})
                        if isinstance(src, dict):
                            if src.get("type") == "base64":
                                url = f"data:{src.get('media_type','image/jpeg')};base64,{src.get('data','')}"
                                parts.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
                            elif src.get("type") == "url":
                                parts.append({"type": "image_url", "image_url": {"url": src.get("url", "")}})
                if len(parts) == 1 and parts[0].get("type") == "text":
                    result.append({"role": "user", "content": parts[0]["text"]})
                elif parts:
                    result.append({"role": "user", "content": parts})

        elif role == "assistant":
            texts = [_bget(b, "text", "") for b in content if _bt(b) == "text"]
            tool_uses = [b for b in content if _bt(b) == "tool_use"]

            out_msg: dict[str, Any] = {
                "role": "assistant",
                "content": " ".join(texts).strip() or None,
            }
            if tool_uses:
                out_msg["tool_calls"] = [
                    {
                        "id": _bget(tu, "id", ""),
                        "type": "function",
                        "function": {
                            "name": _bget(tu, "name", ""),
                            "arguments": json.dumps(_bget(tu, "input", {})),
                        },
                    }
                    for tu in tool_uses
                ]
            result.append(out_msg)

    return result


def _translate_kwargs(kwargs: dict) -> dict:
    """Anthropic messages.create kwargs → OpenAI chat.completions.create kwargs."""
    out: dict = {}
    out["model"] = kwargs.get("model", "gpt-4o")
    out["max_tokens"] = kwargs.get("max_tokens", 4096)

    msgs: list = []
    sys_str = _system_str(kwargs.get("system", ""))
    if sys_str:
        msgs.append({"role": "system", "content": sys_str})
    msgs.extend(_translate_messages(kwargs.get("messages", [])))
    out["messages"] = msgs

    tools = kwargs.get("tools")
    if tools:
        translated = _translate_tools(tools)
        if translated:
            out["tools"] = translated
            out["tool_choice"] = "auto"

    return out


def _wrap_response(resp) -> _FakeMessage:
    choice = resp.choices[0] if resp.choices else None
    content: list = []
    stop_reason = "end_turn"

    if choice:
        m = choice.message
        if m.content:
            content.append(_TextBlock(m.content.strip()))
        if getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                try:
                    inp = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    inp = {}
                content.append(_ToolUseBlock(tc.id, tc.function.name, inp))
            stop_reason = "tool_use"

    usage_obj = getattr(resp, "usage", None)
    return _FakeMessage(
        content,
        stop_reason,
        _Usage(
            getattr(usage_obj, "prompt_tokens", 0) or 0,
            getattr(usage_obj, "completion_tokens", 0) or 0,
        ),
    )


# ── Streaming ─────────────────────────────────────────────────────────────────

class _OpenAIStream:
    """Context manager yielding Anthropic-compatible events from an OpenAI stream."""

    def __init__(self, oai_client, kwargs: dict):
        self._oai = oai_client
        self._kwargs = kwargs
        self._final: _FakeMessage | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        acc_text = ""
        tool_acc: dict[int, dict] = {}
        usage = _Usage(0, 0)

        stream_kwargs = {**self._kwargs, "stream": True, "stream_options": {"include_usage": True}}
        resp = self._oai.chat.completions.create(**stream_kwargs)

        for chunk in resp:
            # Capture usage from the final usage-only chunk
            if not chunk.choices and getattr(chunk, "usage", None):
                u = chunk.usage
                usage = _Usage(
                    getattr(u, "prompt_tokens", 0) or 0,
                    getattr(u, "completion_tokens", 0) or 0,
                )
                continue

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            if delta.content:
                acc_text += delta.content
                yield _Event(delta.content)

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_acc:
                        tool_acc[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_acc[idx]["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if fn.name:
                            tool_acc[idx]["name"] += fn.name
                        if fn.arguments:
                            tool_acc[idx]["args"] += fn.arguments

        blocks: list = []
        if acc_text.strip():
            blocks.append(_TextBlock(acc_text.strip()))
        for idx in sorted(tool_acc):
            tc = tool_acc[idx]
            try:
                inp = json.loads(tc["args"] or "{}")
            except json.JSONDecodeError:
                inp = {}
            blocks.append(_ToolUseBlock(tc["id"], tc["name"], inp))

        self._final = _FakeMessage(blocks, "tool_use" if tool_acc else "end_turn", usage)

    def get_final_message(self) -> _FakeMessage:
        if self._final is None:
            for _ in self:
                pass
        return self._final


# ── Adapter ───────────────────────────────────────────────────────────────────

class _Messages:
    def __init__(self, oai_client, strip_prefix: str = ""):
        self._oai = oai_client
        self._strip = strip_prefix

    def _prep(self, kwargs: dict) -> dict:
        kw = _translate_kwargs(kwargs)
        if self._strip and kw.get("model", "").startswith(self._strip):
            kw["model"] = kw["model"][len(self._strip):]
        return kw

    def create(self, **kwargs) -> _FakeMessage:
        return _wrap_response(self._oai.chat.completions.create(**self._prep(kwargs)))

    def stream(self, **kwargs) -> _OpenAIStream:
        return _OpenAIStream(self._oai, self._prep(kwargs))


class OpenAIAdapter:
    """Drop-in replacement for anthropic.Anthropic() inside AgentCore.

    Works for OpenAI, Google Gemini, and Ollama — all expose an
    OpenAI-compatible endpoint; only base_url, key, and optional prefix differ.
    """

    def __init__(self, api_key: str, base_url: str | None = None, strip_prefix: str = ""):
        from openai import OpenAI
        self._oai = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self.messages = _Messages(self._oai, strip_prefix=strip_prefix)


def get_client(model: str):
    """Return a provider client (Anthropic SDK or OpenAIAdapter) for a model."""
    import config
    p = provider_for(model)
    if p == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=config.API_MAX_RETRIES)
    if p == "gemini":
        return OpenAIAdapter(config.GEMINI_API_KEY, base_url=GEMINI_BASE_URL)
    if p == "ollama":
        # Ollama exposes an OpenAI-compatible API; no real key needed.
        # strip_prefix removes "ollama/" so the model name reaches Ollama correctly.
        return OpenAIAdapter("ollama", base_url=config.OLLAMA_BASE_URL, strip_prefix="ollama/")
    return OpenAIAdapter(config.OPENAI_API_KEY)


def complete(model: str, system: str, user: str, max_tokens: int = 2048) -> str:
    """One-shot text completion against any provider. Returns plain text."""
    client = get_client(model)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


def stream_complete(model: str, system: str, user: str, max_tokens: int = 2048,
                    on_token=None) -> str:
    """Streaming counterpart to complete(). Returns the full text.

    Works across every provider without branching: the Anthropic SDK yields
    `content_block_delta` events natively, and OpenAIAdapter's _OpenAIStream
    fabricates the same shape for OpenAI, Gemini and Ollama.

    Falls back to the blocking path on any streaming failure — a slow answer is
    strictly better than no answer, and the caller cannot tell the difference
    apart from the tokens not arriving early.
    """
    if on_token is None:
        return complete(model, system, user, max_tokens=max_tokens)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        client = get_client(model)
        acc = []
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if getattr(event, "type", "") != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                if delta is None or getattr(delta, "type", "") != "text_delta":
                    continue
                text = getattr(delta, "text", "")
                if not text:
                    continue
                acc.append(text)
                try:
                    on_token(text)
                except Exception:
                    pass          # a broken consumer must not kill generation
        out = "".join(acc).strip()
        if out:
            return out
    except Exception as e:
        print(f"[Provider] streaming failed ({e}); falling back to blocking call")
    return complete(model, system, user, max_tokens=max_tokens)
