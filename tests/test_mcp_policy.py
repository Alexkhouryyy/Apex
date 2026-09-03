"""The gate on third-party MCP servers.

Every outward-facing capability in Apex is deny-by-default with an explicit
allowlist. MCP was the exception, and the worst one to have made: it loads
servers from config files Apex does not own, those tools reach real mail,
calendars and deploys, and `agent/core.py` dispatched `mcp__*` straight through
with a single IoT special-case.

The load-bearing test in this file is `TestTheServerCannotTalkItsWayIn`. A gate
that believes whatever the gated party says about itself is decoration.
"""
import pytest

import config
from agent import mcp_policy as p


class _Ann:
    """Stands in for MCP's ToolAnnotations."""
    def __init__(self, read_only=None, destructive=None):
        self.read_only_hint = read_only
        self.destructive_hint = destructive


@pytest.fixture
def policy(monkeypatch):
    """Known config, so a developer's own .env cannot decide these outcomes —
    the mistake tests/test_handtrack.py already had to be rescued from."""
    monkeypatch.setattr(config, "MCP_POLICY", "ask", raising=False)
    monkeypatch.setattr(config, "MCP_ALLOW", [], raising=False)
    monkeypatch.setattr(config, "MCP_DENY", [], raising=False)
    return config


class TestSplitName:
    def test_it_splits_the_prefixed_form(self):
        assert p.split_name("mcp__slack__send_message") == ("slack", "send_message")

    def test_a_server_name_containing_underscores_survives(self):
        assert p.split_name("mcp__google_calendar__list_events") == (
            "google_calendar", "list_events")

    def test_an_unprefixed_name_does_not_raise(self):
        """This runs on the dispatch path. A gate that throws gets wrapped in a
        bare except by the next person who hits it, and then it is not a gate."""
        assert p.split_name("bash") == ("", "bash")


class TestReadingTheName:
    @pytest.mark.parametrize("name", [
        "get_message", "list_events", "search_threads", "read_note",
        "fetch_transcript", "describe_table", "query_logs", "download_asset",
    ])
    def test_lookers_are_reads(self, name):
        assert p.classify_name(name)[0] == p.READ

    @pytest.mark.parametrize("name", [
        "send_message", "create_event", "delete_file", "update_page",
        "deploy_to_vercel", "buy_domain", "merge_pull_request", "trash_thread",
    ])
    def test_changers_are_writes(self, name):
        assert p.classify_name(name)[0] == p.WRITE

    def test_a_write_verb_anywhere_beats_a_read_verb_in_front(self):
        """`get_or_create_channel` leads with a read verb and creates a channel.
        Matching only the first token would have called it a read."""
        tier, why = p.classify_name("get_or_create_channel")
        assert tier == p.WRITE
        assert "create" in why

    def test_a_read_verb_buried_in_the_middle_does_not_make_it_a_read(self):
        assert p.classify_name("update_search_index")[0] == p.WRITE

    def test_an_unrecognised_verb_is_a_write(self):
        """The default that matters. Any other choice hands every
        newly-invented tool name a free pass, and 'we had not thought of that
        verb yet' is not a safety property."""
        tier, why = p.classify_name("frobnicate_the_widget")
        assert tier == p.WRITE
        assert "not a verb Apex recognises" in why

    def test_an_empty_name_is_a_write(self):
        assert p.classify_name("")[0] == p.WRITE

    def test_camel_case_is_read_too(self):
        assert p.classify_name("sendMessage")[0] == p.WRITE
        assert p.classify_name("listEvents")[0] == p.READ


class TestTheServerCannotTalkItsWayIn:
    """The asymmetry the whole design rests on.

    A server may make Apex's classification STRICTER — it is the only party
    that knows its own tool is destructive. It may never make it looser,
    because that is the direction worth lying about, and the server is exactly
    the party this gate exists to constrain.
    """

    def test_a_server_calling_its_own_send_read_only_is_not_believed(self):
        tier, why = p.classify("send_email", _Ann(read_only=True))
        assert tier == p.WRITE
        assert "does not override" in why

    def test_a_server_calling_its_own_get_destructive_IS_believed(self):
        tier, why = p.classify("get_snapshot", _Ann(destructive=True))
        assert tier == p.WRITE
        assert "destructive" in why

    def test_with_no_annotation_apexs_own_reading_stands(self):
        assert p.classify("list_events", None)[0] == p.READ
        assert p.classify("delete_event", None)[0] == p.WRITE

    def test_a_dict_annotation_in_wire_form_is_read_too(self):
        """MCP's wire format is camelCase; this SDK exposes snake_case
        attributes. Reading only one of them would turn every annotation into a
        silent None — indistinguishable from a server that annotates nothing."""
        assert p.annotation_tier({"destructiveHint": True})[0] == p.WRITE
        assert p.annotation_tier({"readOnlyHint": True})[0] == p.READ

    def test_hints_that_are_false_are_not_treated_as_claims(self):
        """`destructiveHint: False` is the MCP default, not an assertion of
        read-onlyness. Reading it as one would silently widen every tool."""
        assert p.annotation_tier(_Ann(read_only=False, destructive=False))[0] is None


class TestTheDecision:
    def test_reads_run_without_asking(self, policy):
        assert p.decide("mcp__slack__list_channels")["action"] == p.ALLOW

    def test_writes_are_asked_about_by_default(self, policy):
        assert p.decide("mcp__slack__send_message")["action"] == p.ASK

    def test_read_only_policy_refuses_writes_outright(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
        v = p.decide("mcp__slack__send_message")
        assert v["action"] == p.DENY
        assert v["action"] != p.ASK, "read_only must not fall through to a prompt"

    def test_read_only_policy_still_runs_reads(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
        assert p.decide("mcp__slack__list_channels")["action"] == p.ALLOW

    def test_off_refuses_reads_too(self, policy, monkeypatch):
        """`off` has to mean off. A setting that still permits reads would be
        `read_only` under a name that promises more than it does."""
        monkeypatch.setattr(config, "MCP_POLICY", "off", raising=False)
        assert p.decide("mcp__slack__list_channels")["action"] == p.DENY

    def test_all_runs_writes_unasked(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_POLICY", "all", raising=False)
        assert p.decide("mcp__slack__send_message")["action"] == p.ALLOW

    def test_an_allowed_write_stops_asking(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_ALLOW", ["slack:send_message"], raising=False)
        assert p.decide("mcp__slack__send_message")["action"] == p.ALLOW

    def test_a_wildcard_allows_a_whole_server(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_ALLOW", ["slack:*"], raising=False)
        assert p.decide("mcp__slack__send_message")["action"] == p.ALLOW
        assert p.decide("mcp__gmail__send_message")["action"] == p.ASK

    def test_deny_beats_allow(self, policy, monkeypatch):
        """A list of things that must never happen is worthless if another
        setting can outrank it."""
        monkeypatch.setattr(config, "MCP_ALLOW", ["*"], raising=False)
        monkeypatch.setattr(config, "MCP_DENY", ["gmail:send_message"], raising=False)
        assert p.decide("mcp__gmail__send_message")["action"] == p.DENY

    def test_deny_beats_policy_all(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_POLICY", "all", raising=False)
        monkeypatch.setattr(config, "MCP_DENY", ["vercel:*"], raising=False)
        assert p.decide("mcp__vercel__deploy_to_vercel")["action"] == p.DENY

    def test_deny_stops_a_read_as_well_as_a_write(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_DENY", ["gmail:*"], raising=False)
        assert p.decide("mcp__gmail__search_threads")["action"] == p.DENY

    def test_rules_are_case_insensitive(self, policy, monkeypatch):
        monkeypatch.setattr(config, "MCP_DENY", ["GMail:Send_Message"], raising=False)
        assert p.decide("mcp__gmail__send_message")["action"] == p.DENY

    def test_a_comma_string_works_like_a_list(self, policy, monkeypatch):
        """config parses these into a list, but a hand-set value or a future
        settings path may hand over the raw string."""
        monkeypatch.setattr(config, "MCP_DENY", "gmail:*, vercel:*", raising=False)
        assert p.decide("mcp__vercel__list_projects")["action"] == p.DENY


class TestTheRefusalIsUseful:
    def test_it_names_the_rule_and_the_way_out(self, policy, monkeypatch):
        """A gate that says only 'denied' gets switched off wholesale, which is
        worse than no gate."""
        monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
        text = p.refusal(p.decide("mcp__slack__send_message"))
        assert "slack:send_message" in text
        assert "MCP_ALLOW" in text


class TestArgumentsAreNeverStored:
    def test_only_key_names_and_a_hash_come_back(self):
        """MCP arguments carry message bodies, addresses and occasionally
        credentials. An audit trail that quietly becomes a copy of your mail is
        a worse problem than the one it was written to solve."""
        keys, digest = p.fingerprint(
            {"to": "someone@example.com", "body": "the secret is hunter2"})
        assert keys == "body,to"
        assert "hunter2" not in digest and "example.com" not in digest
        assert len(digest) == 16

    def test_the_same_call_hashes_the_same_and_a_different_one_does_not(self):
        a = p.fingerprint({"to": "a@b.c", "body": "x"})[1]
        b = p.fingerprint({"body": "x", "to": "a@b.c"})[1]   # key order differs
        c = p.fingerprint({"to": "a@b.c", "body": "y"})[1]
        assert a == b, "argument order must not change the fingerprint"
        assert a != c, "different arguments must be distinguishable in the log"

    def test_unserialisable_arguments_do_not_raise(self):
        keys, digest = p.fingerprint({"conn": object(), "n": 1})
        assert keys == "conn,n" and digest


class TestTheAudit:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "audit.db"))
        p.init_db()
        return longterm

    def test_refusals_are_recorded_not_only_successes(self, db, policy, monkeypatch):
        """A log of only what happened cannot answer 'what did it try to do',
        which is the question you have after something goes wrong."""
        monkeypatch.setattr(config, "MCP_POLICY", "read_only", raising=False)
        out = p.enforce("mcp__gmail__send_message", {"to": "a@b.c"})
        assert out and "blocked" in out.lower()
        rows = p.recent()
        assert len(rows) == 1
        assert rows[0]["decision"] == "denied"
        assert rows[0]["tool"] == "send_message"

    def test_an_allowed_read_is_recorded_too(self, db, policy):
        assert p.enforce("mcp__gmail__search_threads", {"q": "x"}) is None
        assert p.recent()[0]["decision"] == "allowed"

    def test_the_recorded_row_holds_no_argument_values(self, db, policy):
        p.enforce("mcp__gmail__search_threads", {"q": "my private search"})
        row = p.recent()[0]
        assert "private" not in repr(row)
        assert row["arg_keys"] == "q"

    def test_summary_separates_nothing_refused_from_nothing_recorded(self, db, policy):
        assert p.summary()["total"] == 0
        p.enforce("mcp__gmail__search_threads", {"q": "x"})
        s = p.summary()
        assert s["total"] == 1 and s["by_decision"] == {"allowed": 1}

    def test_an_unwritable_audit_does_not_break_the_tool(self, db, policy, monkeypatch):
        """An audit failure must not become a tool failure, or the first
        unwritable database turns into 'MCP is broken'."""
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", "/nonexistent-dir/x/y.db")
        assert p.enforce("mcp__gmail__search_threads", {"q": "x"}) is None


class TestAskingWhenNobodyCanAnswer:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        from agent import longterm, safety
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "ask.db"))
        monkeypatch.setattr(p, "_confirm_fn", None)
        monkeypatch.setattr(safety, "_confirm_fn", None)
        p.init_db()

    def test_with_nobody_to_ask_a_write_is_refused_not_prompted(self, db, policy):
        """A permission gate that blocks on stdin hangs a daemon, and 'the user
        was not at the keyboard' has never been a reason to permit a write."""
        out = p.enforce("mcp__gmail__send_message", {"to": "a@b.c"})
        assert out and "not approved" in out
        assert p.recent()[0]["decision"] == "asked_denied"

    def test_yes_lets_it_through(self, db, policy, monkeypatch):
        monkeypatch.setattr(p, "_confirm_fn", lambda reason: True)
        assert p.enforce("mcp__gmail__send_message", {"to": "a@b.c"}) is None
        assert p.recent()[0]["decision"] == "asked_allowed"

    def test_it_uses_safetys_confirm_function_when_it_has_none_of_its_own(
            self, db, policy, monkeypatch):
        """Two separate prompting mechanisms would eventually disagree about
        who is allowed to ask. MCP borrows the one Apex already wires up in
        both interactive and resident mode."""
        from agent import safety
        asked = []
        monkeypatch.setattr(safety, "_confirm_fn",
                            lambda reason: asked.append(reason) or True)
        assert p.enforce("mcp__gmail__send_message", {"to": "a@b.c"}) is None
        assert asked and "gmail" in asked[0]

    def test_a_confirm_function_that_raises_refuses(self, db, policy, monkeypatch):
        def boom(reason):
            raise RuntimeError("the console went away")
        monkeypatch.setattr(p, "_confirm_fn", boom)
        assert p.enforce("mcp__gmail__send_message", {"to": "a@b.c"}) is not None


class TestTheDashboardSwitch:
    """A server can be turned off from the dashboard without editing .env and
    without a restart — the same shape as agent/iot.py's kill switch, for the
    same reason: a safety control you have to restart the process to use is one
    nobody uses in the moment they need it.

    The load-bearing rule is that the switch can only ever NARROW.
    """

    @pytest.fixture
    def db(self, tmp_path, monkeypatch, policy):
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "sw.db"))
        monkeypatch.setattr(p, "_switch_cache", None)
        monkeypatch.setattr(p, "_switch_at", 0.0)
        p.init_db()

    def test_a_server_is_on_until_someone_turns_it_off(self, db):
        """A server appearing in a config file is somebody adding it; it should
        work without also being switched on here."""
        assert p.server_enabled("slack") is True
        assert p.decide("mcp__slack__list_channels")["action"] == p.ALLOW

    def test_turning_it_off_refuses_even_a_read(self, db):
        p.set_server_enabled("slack", False)
        v = p.decide("mcp__slack__list_channels")
        assert v["action"] == p.DENY and "switched off" in v["reason"]

    def test_turning_it_back_on_restores_it(self, db):
        p.set_server_enabled("slack", False)
        p.set_server_enabled("slack", True)
        assert p.decide("mcp__slack__list_channels")["action"] == p.ALLOW

    def test_it_survives_a_restart(self, db):
        """Stored in SQLite rather than in memory — a kill switch that forgets
        on restart is one you have to remember to re-flip after every crash."""
        p.set_server_enabled("slack", False)
        p._switch_cache, p._switch_at = {}, 0.0     # a fresh process
        assert p.server_enabled("slack") is False

    def test_it_takes_effect_without_a_restart(self, db):
        """The cache is WARMED first, deliberately.

        The earlier version of this test did not discriminate: with the cache
        starting empty, the first decide() reloaded from SQLite anyway, so
        removing the invalidation entirely still passed. Warming it is what
        makes the assertion about invalidation rather than about a cache that
        was never populated.
        """
        p.set_server_enabled("gmail", True)          # populate the cache
        assert p.decide("mcp__slack__list_channels")["action"] == p.ALLOW
        assert p._switch_cache is not None, "the cache did not warm"

        p.set_server_enabled("slack", False)
        assert p.decide("mcp__slack__list_channels")["action"] == p.DENY, \
            "the cache must be invalidated by the write, not by waiting for it " \
            "to expire — a toggle that takes seconds is one you press twice"

    def test_the_cache_engages_when_nothing_is_switched_off(self, db, monkeypatch):
        """`{}` for "loaded, nothing off" and `{}` for "not loaded" were the same
        value, and since it is falsy the cache never engaged in the common case:
        every turn hit SQLite for an answer that is almost always empty.

        Counts database reads rather than inspecting `_switch_cache`. The first
        version asserted the cache HELD `{}`, which was true whether or not it
        was ever READ — the buggy code still wrote the value, it just never used
        it, so the test passed with the bug reinstated.
        """
        from agent import longterm
        assert p.servers_off() == []               # warm

        reads = []
        real_conn = longterm._conn

        def counting(*a, **k):
            reads.append(1)
            return real_conn(*a, **k)
        monkeypatch.setattr(longterm, "_conn", counting)

        assert p.servers_off() == []
        assert reads == [], (
            "the second read hit the database — an empty result must still "
            "count as loaded, or the cache is dead code exactly when there is "
            "nothing to look up")

    def test_only_the_named_server_is_affected(self, db):
        p.set_server_enabled("slack", False)
        assert p.decide("mcp__gmail__search_threads")["action"] == p.ALLOW

    def test_the_switch_cannot_override_env_deny(self, db, monkeypatch):
        """A control panel that could re-enable something the config file
        forbids would make the config file advisory, and anyone who set
        MCP_DENY meant it."""
        monkeypatch.setattr(config, "MCP_DENY", ["gmail:*"], raising=False)
        out = p.set_server_enabled("gmail", True)
        assert out["enabled"] is True
        assert out["effective"] is False
        assert "MCP_DENY" in out["note"]
        assert p.decide("mcp__gmail__search_threads")["action"] == p.DENY

    def test_it_reports_what_took_effect_not_what_was_asked(self, db, monkeypatch):
        """A toggle that flips in the UI while changing nothing in reality is
        worse than no toggle."""
        monkeypatch.setattr(config, "MCP_DENY", [], raising=False)
        assert p.set_server_enabled("gmail", True)["effective"] is True

    def test_a_missing_table_does_not_disable_everything(self, tmp_path, monkeypatch, policy):
        """Failing open here is deliberate: this switch's job is to let you turn
        things OFF, and a database problem is not you turning something off."""
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "empty.db"))
        monkeypatch.setattr(p, "_switch_cache", None)
        monkeypatch.setattr(p, "_switch_at", 0.0)
        assert p.server_enabled("slack") is True

    def test_an_empty_server_name_is_refused(self, db):
        with pytest.raises(ValueError):
            p.set_server_enabled("   ", False)

    def test_servers_off_lists_only_the_off_ones(self, db):
        p.set_server_enabled("slack", False)
        p.set_server_enabled("gmail", True)
        assert p.servers_off() == ["slack"]


class TestDisabledToolsAreNotOffered:
    """A tool that always refuses still burns context on every turn and invites
    the model to keep trying it, which reads as Apex being broken rather than as
    a setting doing its job.

    This is a convenience, never the protection — a filtered list is trivially
    bypassed by a model that remembers a tool name from earlier in the
    conversation, so `mcp_client.call` gates every call regardless.
    """

    @pytest.fixture
    def core(self, tmp_path, monkeypatch, policy):
        from agent import longterm
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "off.db"))
        monkeypatch.setattr(p, "_switch_cache", None)
        monkeypatch.setattr(p, "_switch_at", 0.0)
        p.init_db()
        from agent.core import AgentCore
        c = AgentCore.__new__(AgentCore)
        c._mcp_tools = [
            {"name": "mcp__slack__send_message"},
            {"name": "mcp__slack__list_channels"},
            {"name": "mcp__gmail__search_threads"},
        ]
        return c

    def test_all_are_offered_when_nothing_is_off(self, core):
        assert len(core._offered_mcp_tools()) == 3

    def test_a_disabled_servers_tools_are_withheld(self, core):
        p.set_server_enabled("slack", False)
        names = [t["name"] for t in core._offered_mcp_tools()]
        assert names == ["mcp__gmail__search_threads"]

    def test_the_call_gate_still_applies_to_a_withheld_tool(self, core):
        """The half that actually protects. A model that remembers the name
        from earlier in the conversation can still ask for it."""
        p.set_server_enabled("slack", False)
        v = p.decide("mcp__slack__list_channels")
        assert v["action"] == p.DENY

    def test_a_failure_does_not_empty_the_toolbox(self, core, monkeypatch):
        """Withholding everything on an error would look exactly like an MCP
        setup that stopped working."""
        monkeypatch.setattr(p, "servers_off",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert len(core._offered_mcp_tools()) == 3
