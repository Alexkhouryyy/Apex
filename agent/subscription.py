"""Run Apex turns on a Claude subscription instead of metered API credits.

The Claude Agent SDK authenticates through the `claude` CLI, so calls made
through it draw on a Pro/Max plan's usage window rather than an API key's
balance. That is the thing this exists for.

## What was measured before building this

The obvious plan — route Apex's cheap, high-volume background calls through the
subscription — is **wrong**, and measurement is the only reason we know:

    call 1: cache_create= 2991  cache_read=23525  cost=$0.0177
    call 2: cache_create= 2984  cache_read=23532  cost=$0.0176

Every SDK call carries ~26.5k tokens of Claude Code's own harness. Prompt caching
brings the marginal cost down to ~$0.0176, but that is still **3x** what the same
work costs on Haiku through the API ($0.0055 for a deep-research extraction). A
1000-source research run routed here would be three times dearer in usage terms
and would flatten a Pro window that is shared with your interactive Claude Code.

So the routing is inverted from the obvious: the *conversation* comes here — one
Opus turn with 85 tools is ~$0.11 on the API and dozens of those a day is real
money — and the cheap background work stays on Haiku, where it is already
cheaper than this could ever be.

## The failure this module is shaped around

First attempt at bridging a tool, verbatim:

    TEXT: Got it, Alex — I'll remember that.
    TOOLS CALLED: []

It said it remembered. It did not. Nothing raised. Under the API path a
`tool_use` block either exists or does not; here the harness decides, and a weak
system prompt gets prose instead of action. That is the exact fail-open shape
behind seventeen findings in this codebase, so `run_turn` reports which tools
actually ran and callers can insist.

## What the SDK owns, and what that costs

The SDK owns the agent loop. Apex's own loop interleaves approvals, restraint and
reranking with tool dispatch, and none of that survives the handover — except
through a `PreToolUse` hook, which is the one point where Apex still gets to say
no. Every dispatch routes through it, and `_permission_hook` explains at length
why the obvious mechanism (`can_use_tool`) is not that point. Anything Apex would
refuse must still be refused.
"""
from __future__ import annotations

import asyncio
import shutil
import threading
from typing import Callable, Optional


# Tools are exposed to the model under an MCP prefix; the model sees
# `mcp__apex__<name>` where Apex calls it `<name>`.
MCP_SERVER = "apex"
_PREFIX = f"mcp__{MCP_SERVER}__"

# Claude Code's harness, measured. Used to explain window consumption rather
# than to guess at it.
HARNESS_TOKENS = 26_500

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """One background loop for the process — the same pattern agent/mcp_client
    uses, and for the same reason: the SDK is async and Apex's call path is not."""
    global _loop
    with _loop_lock:
        if _loop and not _loop.is_closed():
            return _loop
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, daemon=True,
                         name="SubscriptionLoop").start()
        return _loop


def _run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ensure_loop()).result()


def available() -> tuple[bool, str]:
    """(usable, why-not). Checks both halves: the SDK package and the CLI it
    shells out to. Reporting which one is missing saves the obvious support
    question."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False, "claude-agent-sdk is not installed (pip install claude-agent-sdk)"
    if not shutil.which("claude"):
        return False, "the `claude` CLI is not on PATH — install Claude Code and log in"
    return True, ""


def _bridge(tools: list[dict], dispatch: Callable[[str, dict], str]) -> list:
    """Wrap Apex's tool definitions as in-process MCP tools.

    Apex's `input_schema` is full JSON Schema and the SDK accepts it directly —
    verified against the real `remember` schema, including optional fields
    arriving populated.
    """
    import claude_agent_sdk as sdk

    wrapped = []
    for t in tools:
        name = t["name"]

        def _make(tool_name: str):
            async def _call(args: dict) -> dict:
                # Dispatch is Apex's own, so tools behave identically on both
                # paths. Errors come back as text: raising here would surface as
                # an opaque MCP failure instead of something the model can read
                # and recover from, which is how Apex's own loop behaves.
                try:
                    out = await asyncio.to_thread(dispatch, tool_name, args or {})
                except Exception as e:
                    out = f"{type(e).__name__}: {e}"
                return {"content": [{"type": "text", "text": str(out)[:20000]}]}
            return _call

        wrapped.append(
            sdk.tool(name, (t.get("description") or name)[:1000],
                     t.get("input_schema") or {"type": "object", "properties": {}})(
                _make(name))
        )
    return wrapped


def _permission_hook(confirm: Optional[Callable[[str, dict], bool]]):
    """Route every tool the SDK wants to run through Apex's safety layer.

    A **PreToolUse hook**, not `can_use_tool`, and the difference is not
    cosmetic. The first version used `can_use_tool` and the SDK warned:

        can_use_tool will not be invoked for: mcp__apex__remember. An
        allowed_tools entry that allows a whole tool auto-approves it before
        the callback is consulted.

    It was right. With every Apex tool named in `allowed_tools` — which is how
    they are made available at all — the callback never fired. A test refusing
    `remember` was ignored and the tool ran: Apex's approval gate present,
    wired, and silently bypassed. The eighteenth instance of that shape, caught
    by the SDK's own warning rather than by us.

    PreToolUse fires before every call regardless of allow rules, so this is the
    only place Apex can still say no once the SDK owns dispatch.
    """
    async def _pre_tool_use(inp: dict, tool_use_id, context) -> dict:
        tool_name = (inp or {}).get("tool_name", "")
        tool_input = (inp or {}).get("tool_input") or {}
        bare = tool_name[len(_PREFIX):] if tool_name.startswith(_PREFIX) else tool_name

        # Only Apex's own tools are Apex's to judge. Claude Code's built-ins
        # (ToolSearch and friends) are the harness's business.
        if confirm is None or not tool_name.startswith(_PREFIX):
            return {}

        try:
            ok = await asyncio.to_thread(confirm, bare, tool_input)
        except Exception as e:
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Apex safety check failed: {e}"}}
        if ok:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Refused by Apex's safety policy."}}
    return _pre_tool_use


def run_turn(system: str, user_text: str, tools: list[dict],
             dispatch: Callable[[str, dict], str], *,
             model: Optional[str] = None, max_turns: int = 12,
             confirm: Optional[Callable[[str, dict], bool]] = None,
             on_text: Optional[Callable[[str], None]] = None) -> dict:
    """Run one Apex turn through the subscription. Returns a result dict.

    `tools_used` is part of the contract, not diagnostics: the harness decides
    whether to call a tool, and a turn that answered in prose when it should
    have acted is otherwise indistinguishable from one that worked.
    """
    ok, why = available()
    if not ok:
        raise RuntimeError(f"subscription path unavailable: {why}")

    import claude_agent_sdk as sdk

    async def _go() -> dict:
        server = sdk.create_sdk_mcp_server(
            name=MCP_SERVER, version="1.0", tools=_bridge(tools, dispatch))
        opts = sdk.ClaudeAgentOptions(
            system_prompt=system,
            model=model or None,
            mcp_servers={MCP_SERVER: server},
            allowed_tools=[f"{_PREFIX}{t['name']}" for t in tools],
            max_turns=max_turns,
            hooks={"PreToolUse": [sdk.HookMatcher(
                matcher=None, hooks=[_permission_hook(confirm)])]},
            # [] not None: without it the SDK inherits whatever CLAUDE.md and
            # settings happen to sit in the working directory, and Apex's
            # persona would be silently overridden by an unrelated file.
            setting_sources=[],
        )

        chunks: list[str] = []
        used: list[str] = []
        result = None
        async for msg in sdk.query(prompt=user_text, options=opts):
            if isinstance(msg, sdk.AssistantMessage):
                for b in msg.content:
                    if isinstance(b, sdk.TextBlock):
                        if b.text:
                            chunks.append(b.text)
                            if on_text:
                                try:
                                    on_text(b.text)
                                except Exception:
                                    pass
                    elif isinstance(b, sdk.ToolUseBlock):
                        name = b.name
                        used.append(name[len(_PREFIX):]
                                    if name.startswith(_PREFIX) else name)
            elif isinstance(msg, sdk.ResultMessage):
                result = msg

        usage = (getattr(result, "usage", None) or {}) if result else {}
        return {
            "text": "".join(chunks).strip(),
            "tools_used": used,
            "is_error": bool(getattr(result, "is_error", False)) if result else False,
            "turns": getattr(result, "num_turns", 0) if result else 0,
            # What this turn WOULD have cost on the API. Nothing is charged — it
            # is drawn from the plan's window — so recording it is how the saving
            # becomes a measurement instead of a belief.
            "would_have_cost_usd": getattr(result, "total_cost_usd", None) if result else None,
            "usage": usage,
        }

    return _run(_go())


def window_cost(n_calls: int) -> dict:
    """Rough usage-window arithmetic, from the measured harness size.

    Exists because "route everything through the subscription" is tempting and
    wrong, and the reason is a number rather than an opinion.
    """
    tokens = n_calls * HARNESS_TOKENS
    return {
        "calls": n_calls,
        "harness_tokens": tokens,
        "note": (f"{n_calls} calls carry ~{tokens:,} tokens of Claude Code harness "
                 f"before any of your own content. The Pro window is shared with "
                 f"your interactive Claude Code sessions."),
    }
