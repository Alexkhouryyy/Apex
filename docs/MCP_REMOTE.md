# Remote MCP servers

Until now every MCP server Apex could reach was one it **started itself** — a
local process over stdio. A growing number are not: they are URLs you connect
to, authenticated with a header.

> **Not how Apex speaks.** Voicebox's *speech* is wired in directly as a TTS
> engine — `voice/voicebox.py`, `TTS_ENGINE=voicebox`, see
> [VOICEBOX.md](VOICEBOX.md). That is the right shape for a local speech
> service and this page does not change it. What follows is the general
> capability: Apex can use MCP servers it does not start, of which Voicebox's
> `/mcp` endpoint happens to be one.

## Adding one

In `mcp_servers.json`, `.mcp.json`, or `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "voicebox": {
      "url": "http://127.0.0.1:17493/mcp",
      "headers": { "X-Voicebox-Client-Id": "apex" }
    }
  }
}
```

That's it. A `url` means HTTP; a `command` means a local process. You can say
which explicitly with `"type"` (or `"transport"`) — `stdio`, `http` or `sse` —
and the spellings `streamable-http` / `streamable_http` are accepted, since
that is what the MCP docs call it.

`.mcp.json` is on the search path on purpose: `claude mcp add --scope project`
writes that file, so one config serves both Apex and Claude Code rather than
registering the same server twice in two formats.

## Secrets

**`mcp_servers.json` is tracked in git.** A remote server usually authenticates
with a header, and that is a new way for a credential to reach a committed
file — the same mistake this repo already made once with `env`.

Write a placeholder and put the value in `.env`:

```json
"headers": { "Authorization": "Bearer ${VOICEBOX_TOKEN}" }
```

At discovery Apex warns, by name, about any header that *looks like* a
credential (`Authorization`, `X-Api-Key`, anything containing `token`, `secret`,
`password`) and was written out in full. It does not warn about ordinary
headers like `X-Voicebox-Client-Id` — a warning that fires on harmless configs
is one nobody reads by Tuesday.

An unset `${VAR}` in a **url** is refused by name before connecting, because
expanding it to an empty string turns `http://${HOST}/mcp` into `http:///mcp`
and the failure arrives later as a network error blaming the network.

## Nothing changes about permissions

Remote tools go through `mcp_client.call()`, which is where
`agent/mcp_policy.py` sits. Classification, `MCP_ALLOW` / `MCP_DENY`, the
per-server dashboard switch and the `mcp_audit` table all apply exactly as they
do to a local server. A blocked write never opens the transport at all —
`tests/test_mcp_http.py` asserts that against a real server rather than
asserting the refusal string, which a gate that blocked *after* calling would
also satisfy.

The one genuinely new thing to think about is that a remote server is **on a
network**. A local `command` can already run arbitrary code, so it is not a
lower bar — but a URL is reachable by things that are not you, and the header
you send is a credential leaving your machine.

## What is tested, and how

`tests/test_mcp_http.py` runs a **real** MCP server (`FastMCP` behind uvicorn on
a real port) and drives Apex's client at it. That is deliberate: a mocked
transport proves Apex calls the function it calls. It would not have caught
either of the two real bugs found while writing this —

- `streamablehttp_client` yields **three** values where the stdio client yields
  two, so unpacking it the same way fails at the first connection;
- the SDK's newer `streamable_http_client` takes **neither `headers` nor
  `timeout`** — both moved onto an httpx client you build and pass in. Renaming
  the call still connects, still lists tools, still passes a "does it work"
  test, and sends no `Authorization` header at all.

The second one is why the test asserts the header **arrives at the server**,
through an ASGI wrapper that records what it received.

## Not done

- **No OAuth.** The SDK supports it; nothing here wires it up. Header auth only.
- **`websocket` transport is not implemented.** It exists in the SDK, nothing
  writes that config shape, and a transport nobody has ever connected over is a
  claim rather than a feature.
- **The catalogue is still local servers only.** `agent/mcp_catalog.py` installs
  `npx` packages; remote servers are added by editing the config. `probe()`
  handles both, so making the catalogue offer remote entries is a small change
  when there is something to put in it.
