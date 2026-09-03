"""MCP client — discovers and connects to MCP servers configured in Claude Code settings.

Reads mcpServers from ~/.claude/settings.json (or project .claude/settings.json),
starts each server as a subprocess, handshakes, and exposes their tools to the agent.

Tool calls are dispatched synchronously via the MCP SDK's stdio transport.
"""
import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_sessions: dict = {}       # server_name -> (session, tools)
_tool_registry: dict = {}  # full_tool_name -> (server_name, original_tool_name)

# full_tool_name -> the server's own ToolAnnotations (readOnlyHint /
# destructiveHint / ...), kept so agent/mcp_policy.py can take them into
# account. Stored separately from _tool_registry rather than widening its
# tuple: everything that already unpacks that tuple keeps working, and a
# missing annotation stays distinguishable from a missing tool.
_annotations: dict = {}

# What happened to each server, kept after discovery rather than only printed.
#
# A server that fails to start printed one line at boot and then vanished: the
# agent simply had fewer tools than you thought, with nothing anywhere saying
# why. That is this codebase's signature failure — built, wired, silently not
# running — and it is invisible precisely because a missing tool looks like a
# tool the model chose not to use.
_status: dict = {}
_discovered_at: float = 0.0
_ran = False
# Which settings file each server came from. Kept beside the configs rather than
# inside them: the config dict is passed straight to the MCP client, and adding
# our own key to it makes "what did we read?" and "what did we launch?" the same
# object, which they are not.
_source_of: dict = {}


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop
    _loop = asyncio.new_event_loop()

    def runner():
        asyncio.set_event_loop(_loop)
        _loop.run_forever()

    _loop_thread = threading.Thread(target=runner, daemon=True, name="MCPLoop")
    _loop_thread.start()
    return _loop


def _run(coro):
    loop = _ensure_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)


def _find_settings_files() -> list[Path]:
    candidates = [
        Path.cwd() / "mcp_servers.json",                                          # project config (our primary)
        Path.home() / ".claude" / "settings.json",                                # Claude Code
        Path.home() / ".config" / "Claude" / "claude_desktop_config.json",        # Claude Desktop (Linux)
        Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",  # Mac
        Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json",  # Windows
        Path("/home/user/.claude/settings.json"),
    ]
    return [p for p in candidates if p.exists()]


def _load_mcp_configs() -> dict:
    """Return merged mcpServers dict from all settings files. Skips _example_* entries."""
    servers = {}
    for path in _find_settings_files():
        try:
            data = json.loads(path.read_text())
            for k, v in data.get("mcpServers", {}).items():
                if not k.startswith("_"):
                    servers[k] = v
                    _source_of[k] = str(path)
        except Exception as e:
            print(f"[MCP] Could not read {path}: {e}")
    return servers


async def _connect_server(name: str, config: dict) -> list[dict]:
    """Connect to an MCP server, return its tool definitions."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    cmd = config.get("command", "")
    args = config.get("args", [])
    env_extra = config.get("env", {})
    env = {**os.environ, **env_extra}

    params = StdioServerParameters(command=cmd, args=args, env=env)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tools = [
                    {
                        "name": f"mcp__{name}__{t.name}",
                        "description": t.description or "",
                        "input_schema": t.inputSchema or {"type": "object", "properties": {}, "required": []},
                        "_server": name,
                        "_original": t.name,
                        "_annotations": getattr(t, "annotations", None),
                    }
                    for t in (tools_result.tools or [])
                ]
                print(f"[MCP] {name}: {len(tools)} tools")
                _status[name] = {
                    "server": name, "state": "connected",
                    "tools": len(tools),
                    "tool_names": [t["_original"] for t in tools][:60],
                    "command": cmd, "source": _source_of.get(name, ""),
                    "error": "",
                }
                return tools
    except Exception as e:
        print(f"[MCP] {name}: failed to connect — {e}")
        _status[name] = {
            "server": name, "state": "failed", "tools": 0, "tool_names": [],
            "command": cmd, "source": _source_of.get(name, ""),
            "error": f"{type(e).__name__}: {e}",
        }
        return []


async def _call_tool(server_name: str, tool_name: str, inputs: dict, config: dict) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters

    cmd = config.get("command", "")
    args = config.get("args", [])
    env_extra = config.get("env", {})
    env = {**os.environ, **env_extra}

    params = StdioServerParameters(command=cmd, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=inputs)
            parts = []
            for c in (result.content or []):
                if hasattr(c, "text"):
                    parts.append(c.text)
                else:
                    parts.append(str(c))
            return "\n".join(parts) or "(no output)"


# Cached configs to avoid re-reading on every call
_mcp_configs: dict = {}


def discover() -> list[dict]:
    """Connect to all configured MCP servers, return their tool definitions for Claude."""
    global _mcp_configs, _tool_registry, _discovered_at, _ran
    _status.clear()
    _annotations.clear()
    _ran = True
    _discovered_at = time.time()
    _mcp_configs = _load_mcp_configs()

    if not _mcp_configs:
        print("[MCP] No MCP server configs found.")
        return []

    all_tools = []
    for name, config in _mcp_configs.items():
        tools = _run(_connect_server(name, config))
        for t in tools:
            _tool_registry[t["name"]] = (name, t["_original"])
            _annotations[t["name"]] = t.get("_annotations")
        # Strip internal keys before passing to Claude
        for t in tools:
            t.pop("_server", None)
            t.pop("_original", None)
            t.pop("_annotations", None)
        all_tools.extend(tools)

    return all_tools


def call(full_tool_name: str, inputs: dict) -> str:
    """Call an MCP tool by its full prefixed name, subject to the policy gate.

    The gate lives HERE rather than in `agent/core.py`'s `mcp__*` branch, even
    though that is the only caller today. This function is the choke point: a
    dashboard route, a skill or a future dispatcher that reaches an MCP server
    has to come through it, and a gate that only covers one of several doors is
    the same shape as no gate at all.
    """
    if full_tool_name not in _tool_registry:
        return f"Unknown MCP tool: {full_tool_name}"
    server_name, original_name = _tool_registry[full_tool_name]

    from agent import mcp_policy
    blocked = mcp_policy.enforce(full_tool_name, inputs,
                                 _annotations.get(full_tool_name))
    if blocked:
        return blocked

    config = _mcp_configs.get(server_name, {})
    started = time.time()
    verdict = mcp_policy.decide(full_tool_name, _annotations.get(full_tool_name))
    try:
        out = _run(_call_tool(server_name, original_name, inputs, config))
    except Exception as e:
        mcp_policy.record(verdict, inputs, decision="failed",
                          duration_ms=int((time.time() - started) * 1000),
                          ok=False, error=f"{type(e).__name__}: {e}")
        return f"MCP call error ({full_tool_name}): {e}"
    mcp_policy.record(verdict, inputs, decision="completed",
                      duration_ms=int((time.time() - started) * 1000), ok=True)
    return out


def get_registered_names() -> list[str]:
    return list(_tool_registry.keys())


def status() -> dict:
    """What MCP is actually doing, for the dashboard and for `smoke`.

    Three states are reported separately because they need three different
    fixes, and one boolean would flatten them into "no MCP":

      never_ran     — discovery was not called. The tools do not exist and the
                      model was never told about them.
      no_config     — discovery ran and found no `mcpServers` anywhere. Nothing
                      is broken; nothing is configured either.
      ok / degraded — servers were tried. `degraded` means at least one failed,
                      and its exception is kept here rather than left in a boot
                      log that has long since scrolled away.
    """
    servers = sorted(_status.values(), key=lambda s: s["server"])
    failed = [s for s in servers if s["state"] != "connected"]
    if not _ran:
        state = "never_ran"
        detail = ("MCP discovery has not run in this process, so no MCP tool "
                  "exists. In interactive mode it runs at boot; in resident "
                  "mode it runs on a background thread shortly after.")
    elif not servers:
        state = "no_config"
        detail = ("No mcpServers found. Add them to mcp_servers.json in the "
                  "Apex folder, or to ~/.claude/settings.json.")
    elif failed:
        state = "degraded"
        detail = (f"{len(failed)} of {len(servers)} server(s) failed to start. "
                  f"Their tools are missing, which looks identical to the model "
                  f"choosing not to use them.")
    else:
        state = "ok"
        detail = f"{len(servers)} server(s) connected."
    return {
        "state": state,
        "detail": detail,
        "ran": _ran,
        "discovered_at": _discovered_at,
        "servers": servers,
        "tool_count": sum(s["tools"] for s in servers),
        "config_files": [str(p) for p in _find_settings_files()],
    }
