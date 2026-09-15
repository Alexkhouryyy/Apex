"""MCP client — discovers and connects to MCP servers configured in Claude Code settings.

Reads mcpServers from ~/.claude/settings.json (or project .claude/settings.json),
connects to each server, handshakes, and exposes their tools to the agent.

## Two kinds of server, one door

A server is either a LOCAL PROCESS Apex starts (`command`/`args`, stdio) or a
REMOTE ENDPOINT Apex connects to (`url`, streamable HTTP or SSE). Both arrive
through `_transport()`, which every one of the three places that opens a
connection — `probe`, `_connect_server`, `_call_tool` — goes through. Three
copies of "which transport is this" is how one of them ends up supporting a
config shape the other two silently refuse.

The transport is never guessed when the config is ambiguous. A config carrying
both `command` and `url` is refused with its name, because picking one would
mean quietly connecting somewhere the author did not mean.

## Headers carry credentials, and `mcp_servers.json` is in git

A remote server usually authenticates with a header, and that is a new way for
a secret to reach a tracked file — the same mistake this repo already made once
with `env`. Header values get the same `${VAR}` expansion, and a
credential-shaped header written literally is warned about by name at
discovery. See `agent/mcp_catalog.py` for the placeholder convention.
"""
import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
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
        Path.cwd() / ".mcp.json",                                                 # Claude Code project scope
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


_ENV_REF = __import__("re").compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value):
    """Replace ${VAR} with the environment's value, in strings and lists.

    Server configs live in `mcp_servers.json`, which is TRACKED IN GIT, so no
    credential may be written there. Entries reference secrets instead, and the
    real values sit in `.env`. This is where the two meet.

    An unset variable expands to empty rather than raising: the server then
    fails to start with its own error about a missing token, which is a better
    message than a KeyError from Apex's config loader — and
    `agent/mcp_catalog.listing()` already reports which variables are missing
    before you get here.
    """
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _params(config: dict):
    """Build StdioServerParameters from one server config, secrets resolved."""
    from mcp.client.stdio import StdioServerParameters
    return StdioServerParameters(
        command=_expand_env(config.get("command", "")),
        args=_expand_env(config.get("args", [])),
        env={**os.environ, **_expand_env(config.get("env", {}))},
    )


# The transports Apex can open. `websocket` exists in the SDK and is
# deliberately absent: nothing writes that config shape today, and a transport
# nobody has ever connected over is a claim, not a feature.
STDIO, HTTP, SSE = "stdio", "http", "sse"
TRANSPORTS = (STDIO, HTTP, SSE)

# Spellings seen in the wild for the same thing. Claude Code writes `"type"`,
# its CLI flag is `--transport`, and the MCP docs say "streamable HTTP"; a
# config that names the transport correctly must not fail because it hyphenated
# it differently from us.
_TRANSPORT_ALIASES = {
    "stdio": STDIO, "http": HTTP, "https": HTTP,
    "streamable-http": HTTP, "streamable_http": HTTP, "streamablehttp": HTTP,
    "sse": SSE,
}

# Header names that conventionally carry a credential. Deliberately a list of
# names rather than a guess about which VALUES look secret: a heuristic on
# values warns about `X-Client-Id: claude-code` too, and a warning that fires on
# harmless configs is one nobody reads by Tuesday.
_CREDENTIAL_HEADERS = ("authorization", "proxy-authorization", "cookie",
                       "x-api-key", "api-key", "x-auth-token")
_CREDENTIAL_WORDS = ("token", "secret", "password", "apikey", "api_key")


def transport_of(config: dict) -> str:
    """Which transport this config describes. Raises ValueError on ambiguity.

    Order matters: an explicit `type`/`transport` wins, because a config that
    says what it is should not be second-guessed by what other keys happen to
    be present. Only when nothing is declared is it inferred from `url` vs
    `command` — and a config with BOTH is refused rather than resolved, since
    either choice would connect somewhere the author did not ask for.
    """
    declared = str(config.get("type") or config.get("transport") or "").strip().lower()
    if declared:
        kind = _TRANSPORT_ALIASES.get(declared)
        if kind is None:
            raise ValueError(
                f"unknown transport {declared!r}. Apex speaks: {', '.join(TRANSPORTS)}.")
        if kind == STDIO and not config.get("command"):
            raise ValueError("a stdio server needs a 'command'.")
        if kind in (HTTP, SSE) and not config.get("url"):
            raise ValueError(f"a {kind} server needs a 'url'.")
        return kind
    has_url, has_cmd = bool(config.get("url")), bool(config.get("command"))
    if has_url and has_cmd:
        raise ValueError(
            "it has both 'command' and 'url', so Apex cannot tell whether to "
            "start a local process or connect to a remote endpoint. Remove one, "
            "or set 'type' to stdio or http.")
    if has_url:
        return HTTP
    if has_cmd:
        return STDIO
    raise ValueError("it has neither 'command' nor 'url'.")


def _url_of(config: dict) -> str:
    """The endpoint, placeholders resolved, or a refusal naming what is missing.

    The unset-variable check runs BEFORE expansion, and that ordering is the
    whole point. `_expand_env` turns an unset `${MCP_HOST}` into an empty
    string, so `http://${MCP_HOST}/mcp` becomes `http:///mcp` — which still
    starts with `http://` and no longer contains a placeholder to notice. The
    first version of this function checked afterwards and could therefore never
    fire; the connection failed later with a network error blaming the network.
    Found by the test that asserts this refusal, not by reading the code.
    """
    raw = str(config.get("url", "")).strip()
    missing = [v for v in _ENV_REF.findall(raw) if not os.environ.get(v)]
    if missing:
        raise ValueError(
            f"url references {', '.join('${' + v + '}' for v in missing)}, which "
            f"{'is' if len(missing) == 1 else 'are'} not set. Put "
            f"{'it' if len(missing) == 1 else 'them'} in .env.")
    url = str(_expand_env(raw)).strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"url must start with http:// or https://, got {url[:60]!r}")
    return url


def _headers_of(config: dict) -> dict:
    """Header values with `${VAR}` resolved. Pure: warnings live next door."""
    raw = config.get("headers") or {}
    if not isinstance(raw, dict):
        raise ValueError("'headers' must be an object of name -> value.")
    return {str(k): str(v) for k, v in _expand_env(raw).items()}


def literal_credential_headers(config: dict) -> list[str]:
    """Header names that look like credentials and were written out in full.

    Separate from `_headers_of` because it is a DISCOVERY-time warning, not a
    connection-time one: folded into the header builder it fired on every
    single tool call, which is how a warning becomes wallpaper.
    """
    raw = config.get("headers") or {}
    if not isinstance(raw, dict):
        return []
    out = []
    for key, value in raw.items():
        low = str(key).lower()
        if (low in _CREDENTIAL_HEADERS or any(w in low for w in _CREDENTIAL_WORDS)) \
                and "${" not in str(value):
            out.append(str(key))
    return out


def endpoint_of(config: dict) -> str:
    """What this server IS, in one string, for status and error messages."""
    try:
        kind = transport_of(config)
    except ValueError:
        return str(config.get("url") or config.get("command") or "")
    if kind == STDIO:
        args = config.get("args") or []
        return " ".join([str(config.get("command", ""))] + [str(a) for a in args]).strip()
    return str(config.get("url", ""))


@asynccontextmanager
async def _transport(config: dict, *, timeout: float = 30.0):
    """Open one server's transport and yield (read, write).

    The single place that knows how each kind is opened. Note the streamable
    HTTP client yields THREE values, not two — the third is a session-id
    getter — so unpacking it like the stdio client raises ValueError at the
    first connection. That is the kind of thing a wiring diagram hides and a
    real connection finds immediately.
    """
    kind = transport_of(config)
    if kind == STDIO:
        from mcp.client.stdio import stdio_client
        async with stdio_client(_params(config)) as (read, write):
            yield read, write
    elif kind == HTTP:
        import mcp.client.streamable_http as _sh
        url, headers = _url_of(config), _headers_of(config)
        # The SDK renamed this and CHANGED ITS SIGNATURE at the same time: the
        # new `streamable_http_client` takes neither `headers` nor `timeout` —
        # both now live on an httpx client you build and pass in. Swapping the
        # names alone still connects, still passes a "does it list tools" test,
        # and silently sends no Authorization header at all. Found by the test
        # that asserts the header arrives at the server.
        new_api = getattr(_sh, "streamable_http_client", None)
        if new_api is not None:
            import httpx
            from mcp.shared._httpx_utils import create_mcp_http_client
            client = create_mcp_http_client(headers=headers,
                                            timeout=httpx.Timeout(timeout))
            async with new_api(url, http_client=client) as (read, write, _sid):
                yield read, write
        else:
            async with _sh.streamablehttp_client(
                    url, headers=headers, timeout=timeout) as (read, write, _sid):
                yield read, write
    else:
        from mcp.client.sse import sse_client
        async with sse_client(_url_of(config),
                              headers=_headers_of(config),
                              timeout=timeout) as (read, write):
            yield read, write


def probe(config: dict, timeout: float = 180.0) -> tuple:
    """Start a server, handshake, stop. Returns (ok, detail).

    Used by the catalogue before it writes an entry: a package name that has
    moved should fail at the moment you click Install, with the error, rather
    than becoming a config entry that looks right and never connects.
    """
    async def _go():
        from mcp import ClientSession
        async with _transport(config, timeout=min(timeout, 60.0)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return len(tools.tools or [])
    try:
        n = asyncio.run_coroutine_threadsafe(_go(), _ensure_loop()).result(timeout=timeout)
    except TimeoutError:
        # concurrent.futures.TimeoutError arrives with an EMPTY message, so the
        # bare exception renders as "TimeoutError:" and tells nobody anything.
        # Observed on a real first install here: `npx -y <pkg>` downloads the
        # package before it runs, which took longer than the old 60s budget —
        # and the second attempt, with npm's cache warm, took 2.2 seconds.
        return False, (
            f"it did not answer within {timeout:.0f}s. The usual cause is the "
            f"first run of `npx -y <package>`, which downloads before it starts. "
            f"Trying again is often enough. If it keeps timing out, run the "
            f"command by hand to see what it says.")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, f"connected, {n} tool(s)"


async def _connect_server(name: str, config: dict) -> list[dict]:
    """Connect to an MCP server, return its tool definitions."""
    from mcp import ClientSession

    # Built INSIDE the try. It used to sit above it, which was harmless while
    # every config was stdio and nothing here could raise — but transport
    # selection can refuse a config, and an exception escaping this function
    # aborts `discover()`'s whole loop. One malformed server would have taken
    # every other server down with it, and the tools would simply be absent.
    cmd = endpoint_of(config)
    try:
        kind = transport_of(config)
        for header in literal_credential_headers(config):
            print(f"[MCP] {name}: header '{header}' holds a literal value. If this "
                  f"config lives in mcp_servers.json it is tracked by git — put the "
                  f"secret in .env and write ${{VAR_NAME}} here instead.")
        async with _transport(config) as (read, write):
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
                print(f"[MCP] {name}: {len(tools)} tools over {kind}")
                _status[name] = {
                    "server": name, "state": "connected",
                    "tools": len(tools),
                    "tool_names": [t["_original"] for t in tools][:60],
                    "command": cmd, "transport": kind, "endpoint": cmd,
                    "source": _source_of.get(name, ""),
                    "error": "",
                }
                return tools
    except Exception as e:
        print(f"[MCP] {name}: failed to connect — {e}")
        _status[name] = {
            "server": name, "state": "failed", "tools": 0, "tool_names": [],
            "command": cmd, "transport": _safe_transport(config), "endpoint": cmd,
            "source": _source_of.get(name, ""),
            "error": f"{type(e).__name__}: {e}",
        }
        return []


def _safe_transport(config: dict) -> str:
    """The transport name, or "" when the config is the reason we failed."""
    try:
        return transport_of(config)
    except ValueError:
        return ""


async def _call_tool(server_name: str, tool_name: str, inputs: dict, config: dict) -> str:
    from mcp import ClientSession

    async with _transport(config) as (read, write):
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
