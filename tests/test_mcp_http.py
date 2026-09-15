"""MCP over HTTP — a remote endpoint, not a local process.

Until now every MCP server Apex could reach was one it started itself:
`command`, `args`, stdio. A growing number are not — they are URLs you connect
to, authenticated with a header. `voicebox` is one.

The interesting tests here are not "does it connect". They are:

  * **the policy gate still applies.** A new transport is a new way into
    `mcp_client.call()`, and a gate that covers one door is the same shape as
    no gate at all. `TestTheGateDoesNotCareHowYouGotHere` is the reason this
    file exists.
  * **the header actually arrives.** `${VAR}` expansion that silently produced
    an empty Authorization header would look exactly like a server refusing
    the connection for its own reasons.
  * **one bad config does not take the others down.** Transport selection can
    refuse a config, and that refusal used to happen outside the try block that
    protects `discover()`'s loop.

The server in these tests is a REAL MCP server (`FastMCP`) behind a real
uvicorn on a real port. A mocked transport would prove that Apex calls the
function it calls; it would not have caught that `streamablehttp_client` yields
three values where the stdio client yields two, which is the first thing that
breaks.
"""
from __future__ import annotations

import contextlib
import socket
import threading
import time

import pytest

from agent import mcp_client as m


# --------------------------------------------------------------------------
# A real MCP server over real HTTP
# --------------------------------------------------------------------------

class _Capture:
    """ASGI wrapper that records the headers of each request it passes on."""

    def __init__(self, inner):
        self.inner = inner
        self.headers: dict[str, str] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.headers.update({k.decode(): v.decode() for k, v in scope["headers"]})
        await self.inner(scope, receive, send)


@pytest.fixture(scope="module")
def http_server():
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("apex-test-server", stateless_http=True)

    @server.tool()
    def get_status(what: str = "all") -> str:
        """Read something. `get_` makes this a read to agent/mcp_policy."""
        return f"status of {what}: fine"

    @server.tool()
    def delete_everything(confirm: str = "no") -> str:
        """A write, by any reading of the verb."""
        return f"deleted ({confirm})"

    capture = _Capture(server.streamable_http_app())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    uv = uvicorn.Server(uvicorn.Config(capture, host="127.0.0.1", port=port,
                                       log_level="error"))
    threading.Thread(target=uv.run, daemon=True).start()
    for _ in range(100):
        with contextlib.suppress(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        time.sleep(0.05)
    else:
        pytest.fail("the test MCP server never came up")

    yield {"url": f"http://127.0.0.1:{port}/mcp", "capture": capture}
    uv.should_exit = True


_SHARED_STATE = ("_tool_registry", "_annotations", "_status",
                 "_mcp_configs", "_source_of")


@pytest.fixture
def clean_registry():
    """MCP client state is module-level; leave it as it was found.

    Restores by clear()/update() rather than by reassigning the module
    attribute. Rebinding hands back a DIFFERENT dict object, so anything that
    captured a reference to the original keeps writing to an orphan — a
    pollution bug whose symptom appears in some other test file entirely.
    """
    saved = {n: dict(getattr(m, n)) for n in _SHARED_STATE}
    yield
    for name, before in saved.items():
        live = getattr(m, name)
        live.clear()
        live.update(before)


def connect(name: str, config: dict) -> list[dict]:
    tools = m._run(m._connect_server(name, config))
    m._mcp_configs[name] = config
    for t in tools:
        m._tool_registry[t["name"]] = (name, t["_original"])
        m._annotations[t["name"]] = t.get("_annotations")
    return tools


# --------------------------------------------------------------------------
# Transport selection
# --------------------------------------------------------------------------

class TestTransportSelection:

    def test_a_url_means_http(self):
        assert m.transport_of({"url": "http://x/mcp"}) == m.HTTP

    def test_a_command_means_stdio(self):
        assert m.transport_of({"command": "npx", "args": ["-y", "x"]}) == m.STDIO

    @pytest.mark.parametrize("declared", [
        "http", "https", "streamable-http", "streamable_http", "streamablehttp", "HTTP"])
    def test_the_spellings_people_actually_write(self, declared):
        """Claude Code writes `type`, its CLI flag is `--transport`, and the
        docs say "streamable HTTP". A config that names the transport correctly
        must not fail because it hyphenated it differently from us."""
        assert m.transport_of({"type": declared, "url": "http://x/mcp"}) == m.HTTP
        assert m.transport_of({"transport": declared, "url": "http://x/mcp"}) == m.HTTP

    def test_sse_is_separate_from_http(self):
        assert m.transport_of({"type": "sse", "url": "http://x/sse"}) == m.SSE

    def test_an_explicit_type_wins_over_what_other_keys_suggest(self):
        assert m.transport_of({"type": "stdio", "command": "x", "url": ""}) == m.STDIO

    def test_both_command_and_url_is_refused_rather_than_resolved(self):
        """Either choice would connect somewhere the author did not ask for."""
        with pytest.raises(ValueError) as e:
            m.transport_of({"command": "npx", "url": "http://x/mcp"})
        assert "cannot tell" in str(e.value)

    def test_neither_is_refused(self):
        with pytest.raises(ValueError):
            m.transport_of({"env": {}})

    def test_a_declared_transport_missing_its_field_is_refused(self):
        with pytest.raises(ValueError) as e:
            m.transport_of({"type": "http"})
        assert "needs a 'url'" in str(e.value)
        with pytest.raises(ValueError) as e:
            m.transport_of({"type": "stdio"})
        assert "needs a 'command'" in str(e.value)

    def test_an_unknown_transport_names_the_ones_that_exist(self):
        with pytest.raises(ValueError) as e:
            m.transport_of({"type": "carrier-pigeon", "url": "http://x"})
        assert "stdio" in str(e.value) and "http" in str(e.value)

    @pytest.mark.parametrize("url", ["ftp://x/mcp", "file:///etc/passwd", "x/mcp", ""])
    def test_only_http_urls_are_accepted(self, url):
        with pytest.raises(ValueError):
            m._url_of({"url": url})

    def test_an_unresolved_placeholder_in_a_url_is_refused_by_name(self, monkeypatch):
        """`_expand_env` turns an unset `${MCP_HOST}` into "", so
        `http://${MCP_HOST}/mcp` becomes `http:///mcp` — still starting with
        http://, with no placeholder left to notice. A check that ran after
        expansion could never fire, and the failure arrived later as a network
        error blaming the network. The refusal names the variable, because
        "something is unset" is not an instruction."""
        monkeypatch.delenv("APEX_TEST_MCP_HOST", raising=False)
        with pytest.raises(ValueError) as e:
            m._url_of({"url": "http://${APEX_TEST_MCP_HOST}/mcp"})
        assert "APEX_TEST_MCP_HOST" in str(e.value)

    def test_a_set_placeholder_resolves(self, monkeypatch):
        monkeypatch.setenv("APEX_TEST_MCP_HOST", "127.0.0.1:9000")
        assert m._url_of({"url": "http://${APEX_TEST_MCP_HOST}/mcp"}) == \
            "http://127.0.0.1:9000/mcp"

    def test_the_endpoint_is_reported_for_both_kinds(self):
        assert m.endpoint_of({"url": "http://x/mcp"}) == "http://x/mcp"
        assert m.endpoint_of({"command": "npx", "args": ["-y", "pkg"]}) == "npx -y pkg"


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------

class TestHeaders:

    def test_placeholders_are_resolved_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("APEX_TEST_MCP_TOKEN", "sk-real-value")
        headers = m._headers_of({"headers": {"Authorization": "Bearer ${APEX_TEST_MCP_TOKEN}"}})
        assert headers["Authorization"] == "Bearer sk-real-value"

    def test_a_literal_credential_header_is_named(self):
        """mcp_servers.json is tracked in git. This repo already made this
        mistake once with `env`; headers are a second door to it."""
        assert m.literal_credential_headers(
            {"headers": {"Authorization": "Bearer sk-live-abc"}}) == ["Authorization"]
        assert m.literal_credential_headers(
            {"headers": {"X-Api-Token": "abc123"}}) == ["X-Api-Token"]

    def test_a_placeholder_credential_header_is_not_warned_about(self):
        assert m.literal_credential_headers(
            {"headers": {"Authorization": "Bearer ${TOK}"}}) == []

    def test_an_ordinary_header_is_not_warned_about(self):
        """Voicebox's own header. A warning that fires on harmless configs is
        one nobody reads by Tuesday."""
        assert m.literal_credential_headers(
            {"headers": {"X-Voicebox-Client-Id": "claude-code"}}) == []

    def test_headers_must_be_an_object(self):
        with pytest.raises(ValueError):
            m._headers_of({"headers": ["Authorization: x"]})


# --------------------------------------------------------------------------
# A real connection
# --------------------------------------------------------------------------

class TestARealHttpServer:

    def test_it_connects_and_lists_tools(self, http_server, clean_registry):
        tools = connect("probe", {"url": http_server["url"]})
        assert {t["name"] for t in tools} == {
            "mcp__probe__get_status", "mcp__probe__delete_everything"}

    def test_the_status_says_which_transport_and_where(self, http_server, clean_registry):
        connect("probe", {"url": http_server["url"]})
        st = m._status["probe"]
        assert st["state"] == "connected"
        assert st["transport"] == m.HTTP
        assert st["endpoint"] == http_server["url"]

    def test_probe_handshakes_before_anything_is_written(self, http_server):
        ok, detail = m.probe({"url": http_server["url"]}, timeout=30)
        assert ok, detail
        assert "2 tool" in detail

    def test_probe_fails_on_a_dead_endpoint_with_the_reason(self):
        ok, detail = m.probe({"url": "http://127.0.0.1:1/mcp"}, timeout=20)
        assert not ok and detail

    def test_the_configured_header_reaches_the_server(self, http_server, clean_registry, monkeypatch):
        """An expansion that silently produced an empty header would look
        exactly like the server rejecting the connection on its own terms."""
        monkeypatch.setenv("APEX_TEST_MCP_ID", "apex-was-here")
        http_server["capture"].headers.clear()
        connect("probe", {"url": http_server["url"],
                          "headers": {"X-Voicebox-Client-Id": "${APEX_TEST_MCP_ID}"}})
        assert http_server["capture"].headers.get("x-voicebox-client-id") == "apex-was-here"


class TestTheGateDoesNotCareHowYouGotHere:
    """The reason this file exists. A new transport is a new way into
    `mcp_client.call()`; a gate that covers one door is no gate."""

    def test_a_read_over_http_runs(self, http_server, clean_registry, monkeypatch):
        import config
        monkeypatch.setattr(config, "MCP_POLICY", "ask")
        connect("probe", {"url": http_server["url"]})
        out = m.call("mcp__probe__get_status", {"what": "batteries"})
        assert "status of batteries: fine" in out

    def test_a_write_over_http_is_blocked_exactly_as_over_stdio(
            self, http_server, clean_registry, monkeypatch):
        import config
        monkeypatch.setattr(config, "MCP_POLICY", "read_only")
        connect("probe", {"url": http_server["url"]})
        out = m.call("mcp__probe__delete_everything", {"confirm": "yes"})
        assert "[MCP blocked]" in out

    def test_a_blocked_write_never_reaches_the_server(
            self, http_server, clean_registry, monkeypatch):
        """Asserting the refusal string is not enough — it is satisfied by a
        gate that blocks after the call. The server must not be touched."""
        import config
        monkeypatch.setattr(config, "MCP_POLICY", "read_only")
        connect("probe", {"url": http_server["url"]})
        http_server["capture"].headers.clear()
        m.call("mcp__probe__delete_everything", {"confirm": "yes"})
        assert http_server["capture"].headers == {}, (
            "the transport was opened for a call the policy refused")

    def test_deny_outranks_everything_over_http(self, http_server, clean_registry, monkeypatch):
        import config
        monkeypatch.setattr(config, "MCP_POLICY", "all")
        monkeypatch.setattr(config, "MCP_DENY", ["probe:get_status"])
        connect("probe", {"url": http_server["url"]})
        assert "[MCP blocked]" in m.call("mcp__probe__get_status", {"what": "x"})


# --------------------------------------------------------------------------
# Discovery robustness
# --------------------------------------------------------------------------

class TestOneBadConfigDoesNotTakeTheRestDown:

    def test_a_refused_config_is_recorded_as_failed_not_raised(self, clean_registry):
        """Transport selection can refuse a config. It used to be built ABOVE
        the try block, so that refusal would have escaped `_connect_server` and
        aborted `discover()`'s whole loop — every other server's tools simply
        absent, with one traceback to explain it."""
        tools = m._run(m._connect_server("broken", {"command": "npx", "url": "http://x/mcp"}))
        assert tools == []
        assert m._status["broken"]["state"] == "failed"
        assert "cannot tell" in m._status["broken"]["error"]

    def test_the_good_server_still_connects_alongside_a_broken_one(
            self, http_server, clean_registry, monkeypatch):
        monkeypatch.setattr(m, "_load_mcp_configs",
                            lambda: {"broken": {"nothing": True},
                                     "probe": {"url": http_server["url"]}})
        tools = m.discover()
        assert any(t["name"] == "mcp__probe__get_status" for t in tools)
        assert m._status["broken"]["state"] == "failed"
        assert m._status["probe"]["state"] == "connected"

    def test_status_reports_degraded_rather_than_hiding_the_failure(
            self, http_server, clean_registry, monkeypatch):
        monkeypatch.setattr(m, "_load_mcp_configs",
                            lambda: {"broken": {"nothing": True},
                                     "probe": {"url": http_server["url"]}})
        m.discover()
        assert m.status()["state"] == "degraded"


class TestTheProjectConfigIsRead:

    def test_claude_codes_project_file_is_on_the_search_path(self, tmp_path, monkeypatch):
        """`claude mcp add --scope project` writes `.mcp.json`. Reading it means
        one file configures both Apex and Claude Code, rather than the same
        server being registered twice in two formats."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".mcp.json").write_text(
            '{"mcpServers": {"voicebox": {"type": "http", '
            '"url": "http://127.0.0.1:17493/mcp"}}}')
        assert tmp_path / ".mcp.json" in m._find_settings_files()
        cfgs = m._load_mcp_configs()
        assert m.transport_of(cfgs["voicebox"]) == m.HTTP
