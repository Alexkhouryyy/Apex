"""MCP discovery and dispatch, imported at module load and never tested.

`agent/mcp_client.py` is imported at `agent/core.py:9`, `discover()` runs at
every boot, and `call()` dispatches every MCP tool the model uses. It was an
UNPROVEN row in docs/APEX_GAP_ANALYSIS.md.

Spawning real MCP servers is out of reach here, but the two halves that decide
whether anything works at all are pure: finding and merging the settings files,
and refusing a tool that was never registered. Both fail quietly by design —
discovery prints and returns [], dispatch returns an error string — so a break
in either looks exactly like "you have no MCP servers configured".
"""
from __future__ import annotations

import json

import pytest

from agent import mcp_client


@pytest.fixture(autouse=True)
def clean_registry():
    mcp_client._tool_registry.clear()
    mcp_client._mcp_configs.clear()
    yield
    mcp_client._tool_registry.clear()
    mcp_client._mcp_configs.clear()


def _settings(tmp_path, servers: dict, name="mcp_servers.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"mcpServers": servers}))
    return p


# ── finding and merging config ────────────────────────────────────────────────

def test_reads_servers_from_a_settings_file(tmp_path, monkeypatch):
    path = _settings(tmp_path, {"weather": {"command": "weather-mcp"}})
    monkeypatch.setattr(mcp_client, "_find_settings_files", lambda: [path])
    assert mcp_client._load_mcp_configs() == {"weather": {"command": "weather-mcp"}}


def test_example_entries_are_skipped(tmp_path, monkeypatch):
    """Shipped config carries `_example_*` templates. Starting those as real
    servers would spawn subprocesses for things that do not exist."""
    path = _settings(tmp_path, {
        "_example_thing": {"command": "nope"},
        "real": {"command": "yes"},
    })
    monkeypatch.setattr(mcp_client, "_find_settings_files", lambda: [path])
    assert list(mcp_client._load_mcp_configs()) == ["real"]


def test_later_files_win_on_a_name_clash(tmp_path, monkeypatch):
    a = _settings(tmp_path, {"shared": {"command": "first"}}, "a.json")
    b = _settings(tmp_path, {"shared": {"command": "second"}}, "b.json")
    monkeypatch.setattr(mcp_client, "_find_settings_files", lambda: [a, b])
    assert mcp_client._load_mcp_configs()["shared"]["command"] == "second"


def test_malformed_json_does_not_take_the_others_down(tmp_path, monkeypatch, capsys):
    """One unreadable settings file must not cost you every other server —
    and it must say so, because the alternative is silently fewer tools."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json at all")
    good = _settings(tmp_path, {"real": {"command": "yes"}}, "good.json")
    monkeypatch.setattr(mcp_client, "_find_settings_files", lambda: [bad, good])

    assert list(mcp_client._load_mcp_configs()) == ["real"]
    assert "Could not read" in capsys.readouterr().out


def test_missing_files_are_not_returned():
    """_find_settings_files filters on .exists(); a missing path reaching
    _load_mcp_configs would print a spurious error every boot."""
    assert all(p.exists() for p in mcp_client._find_settings_files())


def test_no_config_means_no_tools_and_a_clear_line(monkeypatch, capsys):
    monkeypatch.setattr(mcp_client, "_load_mcp_configs", dict)
    assert mcp_client.discover() == []
    assert "No MCP server configs found" in capsys.readouterr().out


# ── dispatch ──────────────────────────────────────────────────────────────────

def test_an_unregistered_tool_is_refused_not_raised():
    """core dispatches straight into this; a raise would end the turn."""
    out = mcp_client.call("nonexistent__tool", {})
    assert "Unknown MCP tool" in out


def test_a_failing_server_returns_an_error_string(monkeypatch, tmp_path):
    """The name here is the one `_connect_server` actually builds
    (`mcp__<server>__<tool>`); the earlier fixture used a shorter form that
    nothing produces. That mattered once the policy gate existed: an
    unparseable name has no server, and `do` is not a verb Apex recognises, so
    the call was refused before it could fail — and this test then proved the
    refusal path rather than the transport one it is named for.

    The policy is pinned for the same reason: an ambient MCP_POLICY in the
    developer's own .env must not decide whether this passes."""
    import config
    from agent import longterm, mcp_policy
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "mcp.db"))
    monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
    monkeypatch.setattr(config, "MCP_ALLOW", [], raising=False)
    monkeypatch.setattr(config, "MCP_DENY", [], raising=False)
    mcp_policy.init_db()

    mcp_client._tool_registry["mcp__srv__get_thing"] = ("srv", "get_thing")
    mcp_client._mcp_configs["srv"] = {"command": "x"}

    def _boom(*a, **k):
        raise RuntimeError("server died")
    monkeypatch.setattr(mcp_client, "_call_tool", lambda *a, **k: None)
    monkeypatch.setattr(mcp_client, "_run", _boom)

    out = mcp_client.call("mcp__srv__get_thing", {})
    assert "MCP call error" in out and "server died" in out


def test_registered_names_match_what_was_registered():
    mcp_client._tool_registry["srv__alpha"] = ("srv", "alpha")
    mcp_client._tool_registry["srv__beta"] = ("srv", "beta")
    assert sorted(mcp_client.get_registered_names()) == ["srv__alpha", "srv__beta"]


def test_discover_registers_tools_and_strips_internal_keys(monkeypatch):
    """`_server` and `_original` are bookkeeping. Leaving them on the tool
    definition sends unknown fields to the API."""
    monkeypatch.setattr(mcp_client, "_load_mcp_configs",
                        lambda: {"srv": {"command": "x"}})
    # Stub _connect_server too, not just _run: replacing only _run leaves the
    # coroutine created and never awaited, which pytest reports as a warning.
    monkeypatch.setattr(mcp_client, "_connect_server", lambda name, cfg: None)
    monkeypatch.setattr(mcp_client, "_run", lambda coro: [
        {"name": "srv__alpha", "description": "d",
         "input_schema": {"type": "object"}, "_server": "srv", "_original": "alpha"},
    ])
    tools = mcp_client.discover()
    assert [t["name"] for t in tools] == ["srv__alpha"]
    assert "_server" not in tools[0] and "_original" not in tools[0]
    assert mcp_client._tool_registry["srv__alpha"] == ("srv", "alpha")


class TestTheGateIsAtTheChokePoint:
    """`mcp_policy.enforce` runs inside `call()`, not in `agent/core.py`'s
    `mcp__*` branch.

    core.py is the only caller today, and putting the gate there would have
    been simpler. But `call()` is the single door: a dashboard route, a skill,
    or a future dispatcher reaching an MCP server has to come through it. A
    gate that covers one of several doors is the same shape as no gate — which
    is what this codebase kept finding elsewhere all session.
    """

    @pytest.fixture
    def registered(self, tmp_path, monkeypatch):
        import config
        from agent import longterm, mcp_policy
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "gate.db"))
        monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
        monkeypatch.setattr(config, "MCP_ALLOW", [], raising=False)
        monkeypatch.setattr(config, "MCP_DENY", [], raising=False)
        mcp_policy.init_db()
        mcp_client._tool_registry["mcp__gmail__send_message"] = ("gmail", "send_message")
        mcp_client._tool_registry["mcp__gmail__search_threads"] = ("gmail", "search_threads")
        mcp_client._mcp_configs["gmail"] = {"command": "true", "args": []}
        return mcp_policy

    def test_a_refused_write_never_reaches_the_transport(self, registered, monkeypatch):
        """The assertion that matters. Not "it returned a refusal string" —
        that a refused call did not also run."""
        ran = []
        monkeypatch.setattr(mcp_client, "_run",
                            lambda coro: ran.append(coro) or "sent!")
        out = mcp_client.call("mcp__gmail__send_message", {"to": "a@b.c"})
        assert "blocked" in out.lower()
        assert ran == [], "the tool ran anyway — the gate is decorative"

    def test_an_allowed_read_does_reach_the_transport(self, registered, monkeypatch):
        """The other half. A gate that blocks everything passes the test above
        and is useless."""
        monkeypatch.setattr(mcp_client, "_run", lambda coro: "three threads")
        assert mcp_client.call("mcp__gmail__search_threads", {"q": "x"}) == "three threads"

    def test_an_unknown_tool_is_refused_before_the_policy_sees_it(self, registered):
        assert "Unknown MCP tool" in mcp_client.call("mcp__gmail__nope", {})

    def test_a_completed_call_is_audited(self, registered, monkeypatch):
        monkeypatch.setattr(mcp_client, "_run", lambda coro: "ok")
        mcp_client.call("mcp__gmail__search_threads", {"q": "x"})
        decisions = [r["decision"] for r in registered.recent()]
        assert "completed" in decisions, \
            "an outcome row is what distinguishes 'allowed' from 'actually ran'"

    def test_a_transport_failure_is_audited_as_a_failure(self, registered, monkeypatch):
        def boom(coro):
            raise RuntimeError("server died")
        monkeypatch.setattr(mcp_client, "_run", boom)
        out = mcp_client.call("mcp__gmail__search_threads", {"q": "x"})
        assert "MCP call error" in out
        row = registered.recent()[0]
        assert row["decision"] == "failed" and "server died" in row["error"]
