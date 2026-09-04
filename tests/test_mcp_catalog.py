"""Adding an MCP server without editing JSON — and without committing a token.

Apex could always USE any MCP server and had no way to ADD one, which is why
the shipped mcp_servers.json still contained nothing but `_example_` entries
that mcp_client skips. The honest count of configured servers was zero.

`TestSecretsNeverReachTheTrackedFile` is the load-bearing class. mcp_servers.json
is in git; a Slack token written there is a committed Slack token, and the
person who did it would not find out until it was in the history.
"""
import json
import os
from pathlib import Path

import pytest

from agent import mcp_catalog as cat

OK = lambda launch: (True, "connected, 3 tool(s)")          # noqa: E731


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mcp_servers.json").write_text(
        json.dumps({"mcpServers": {"_example_x": {"command": "npx"}}}) + "\n")
    return tmp_path


def _servers(root):
    return json.loads((root / "mcp_servers.json").read_text())["mcpServers"]


class TestTheTrackedFileIsWhyThisExists:
    def test_mcp_servers_json_really_is_in_git(self):
        """If this ever stops being true the indirection is still fine, but the
        reasoning in the module docstring would be wrong — and a comment that
        explains a constraint that no longer exists is how the constraint gets
        removed."""
        import subprocess
        repo = Path(__file__).resolve().parent.parent
        out = subprocess.run(["git", "ls-files", "--error-unmatch",
                              "mcp_servers.json"],
                             cwd=repo, capture_output=True)
        assert out.returncode == 0, \
            "mcp_servers.json is no longer tracked — revisit agent/mcp_catalog"

    def test_the_shipped_file_is_a_template_not_a_configuration(self):
        """The answer to "why do I have no MCP servers".

        Every key in the shipped file is `_`-prefixed and mcp_client skips
        those, so a file that reads like five servers is zero servers. This
        stays true on purpose: the repo must not ship someone else's server
        configuration, and if a real key ever appears here it arrived by
        accident.

        (The first version of this assertion was `all(...) or real`, which is
        true for every possible input. It tested nothing.)
        """
        repo = Path(__file__).resolve().parent.parent
        data = json.loads((repo / "mcp_servers.json").read_text())
        real = [k for k in data["mcpServers"] if not k.startswith("_")]
        assert real == [], (
            f"mcp_servers.json ships with live server(s) {real}. The repo must "
            f"not carry a configuration; it carries examples.")


class TestSecretsNeverReachTheTrackedFile:
    def test_a_credential_is_written_to_env_not_to_the_config(self, root):
        cat.install("slack", {"SLACK_BOT_TOKEN": "xoxb-real-secret",
                              "SLACK_TEAM_ID": "T123"}, verify=OK)
        raw = (root / "mcp_servers.json").read_text()
        assert "xoxb-real-secret" not in raw
        assert "${SLACK_BOT_TOKEN}" in raw
        assert "xoxb-real-secret" in (root / ".env").read_text()

    def test_the_config_holds_only_placeholders(self, root):
        cat.install("slack", {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_TEAM_ID": "T1"},
                    verify=OK)
        env = _servers(root)["slack"]["env"]
        assert env == {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}",
                       "SLACK_TEAM_ID": "${SLACK_TEAM_ID}"}

    def test_a_literal_secret_is_refused_outright(self, root, monkeypatch):
        """"Be careful" is not a mechanism. If some future path tried to write a
        real value into the launch config, this stops it."""
        with pytest.raises(cat.InstallRefused) as e:
            cat._check_secrets({"SLACK_BOT_TOKEN": "xoxb-1234567890"})
        assert "tracked in git" in str(e.value)

    def test_placeholders_are_not_mistaken_for_secrets(self, root):
        cat._check_secrets({"A": "${A}", "B": "${LONG_VARIABLE_NAME}"})

    def test_uninstall_leaves_the_credential_alone(self, root):
        """A Remove button silently deleting a token you use elsewhere is not
        something a Remove button should do."""
        cat.install("slack", {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_TEAM_ID": "T1"},
                    verify=OK)
        cat.uninstall("slack")
        assert "xoxb-x" in (root / ".env").read_text()
        assert "slack" not in _servers(root)


class TestInstallIsVerifiedNotAssumed:
    def test_a_server_that_will_not_start_is_not_written(self, root):
        """A catalogue is a snapshot of the day it was written; package names
        move. A wrong one must fail when you click, not become an entry that
        looks configured and never connects."""
        with pytest.raises(cat.InstallRefused) as e:
            cat.install("filesystem", verify=lambda l: (False, "npm ENOTFOUND"))
        assert "did not start" in str(e.value) and "ENOTFOUND" in str(e.value)
        assert "filesystem" not in _servers(root)

    def test_a_failed_install_keeps_the_credential_it_was_given(self, root):
        """So a retry does not ask for the token again."""
        with pytest.raises(cat.InstallRefused):
            cat.install("slack", {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_TEAM_ID": "T1"},
                        verify=lambda l: (False, "boom"))
        assert "xoxb-x" in (root / ".env").read_text()

    def test_a_successful_install_lands_in_the_config(self, root):
        out = cat.install("filesystem", verify=OK)
        assert out["ok"] is True
        entry = _servers(root)["filesystem"]
        assert entry["command"] == "npx"
        assert "@modelcontextprotocol/server-filesystem" in entry["args"]

    def test_the_examples_are_left_untouched(self, root):
        cat.install("filesystem", verify=OK)
        assert "_example_x" in _servers(root)


class TestMissingCredentialsAreCaughtBeforeLaunch:
    def test_installing_without_a_required_key_is_refused(self, root, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_TEAM_ID", raising=False)
        ran = []
        with pytest.raises(cat.InstallRefused) as e:
            cat.install("slack", {}, verify=lambda l: ran.append(1) or (True, ""))
        assert "SLACK_BOT_TOKEN" in str(e.value)
        assert ran == [], "it should not launch a server it knows will fail"

    def test_a_key_already_in_the_environment_counts(self, root, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
        monkeypatch.setenv("SLACK_TEAM_ID", "T9")
        assert cat.install("slack", {}, verify=OK)["ok"] is True
        assert "xoxb-from-env" not in (root / "mcp_servers.json").read_text()

    def test_a_server_needing_nothing_installs_in_one_step(self, root):
        assert cat.install("filesystem", verify=OK)["ok"] is True


class TestListing:
    def test_it_reports_what_is_installed(self, root):
        assert cat.listing()[0]["installed"] is False
        cat.install("filesystem", verify=OK)
        got = {e["id"]: e["installed"] for e in cat.listing()}
        assert got["filesystem"] is True and got["slack"] is False

    def test_it_reports_which_credentials_are_still_missing(self, root, monkeypatch):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        e = next(x for x in cat.listing() if x["id"] == "brave-search")
        assert e["missing"] == ["BRAVE_API_KEY"]
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        e = next(x for x in cat.listing() if x["id"] == "brave-search")
        assert e["missing"] == []

    def test_underscore_entries_do_not_count_as_installed(self, root):
        """The bug being explained: five `_example_` keys look like five
        servers and are zero."""
        assert cat.installed() == []

    def test_every_entry_is_well_formed(self):
        """A catalogue row missing a command is a button that cannot work."""
        for e in cat.CATALOG:
            assert e["id"] and e["name"] and e["blurb"] and e["command"]
            assert isinstance(e["args"], list) and e["args"]
            assert isinstance(e["env"], dict)
            for var, why in e["env"].items():
                assert var.isupper() and why, f"{e['id']}: {var} has no guidance"

    def test_ids_are_unique(self):
        ids = [e["id"] for e in cat.CATALOG]
        assert len(ids) == len(set(ids))


class TestRefusals:
    def test_an_unknown_id_is_refused(self, root):
        with pytest.raises(cat.InstallRefused):
            cat.install("not-a-real-server", verify=OK)

    def test_uninstalling_something_absent_says_so(self, root):
        out = cat.uninstall("nope")
        assert out["ok"] is False and "not installed" in out["error"]

    def test_a_corrupt_config_is_not_overwritten(self, root):
        """Rewriting it would throw away whatever is in there."""
        (root / "mcp_servers.json").write_text("{ this is not json")
        with pytest.raises(cat.InstallRefused) as e:
            cat.install("filesystem", verify=OK)
        assert "not valid JSON" in str(e.value)
        assert (root / "mcp_servers.json").read_text() == "{ this is not json"


class TestEnvExpansionAtLaunch:
    """Where the indirection is actually cashed in."""

    def test_a_placeholder_becomes_the_real_value(self, monkeypatch):
        from agent import mcp_client
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-live")
        p = mcp_client._params({"command": "npx", "args": ["-y", "x"],
                                "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}"}})
        assert p.env["SLACK_BOT_TOKEN"] == "xoxb-live"

    def test_it_expands_inside_arguments_too(self, monkeypatch):
        from agent import mcp_client
        monkeypatch.setenv("POSTGRES_URL", "postgresql://u@h/db")
        p = mcp_client._params({"command": "npx",
                                "args": ["-y", "srv", "${POSTGRES_URL}"]})
        assert p.args[-1] == "postgresql://u@h/db"

    def test_an_unset_variable_expands_to_empty_rather_than_raising(self, monkeypatch):
        """The server then fails with its own message about a missing token,
        which is a better error than a KeyError from Apex's config loader."""
        from agent import mcp_client
        monkeypatch.delenv("NOPE_NOT_SET", raising=False)
        p = mcp_client._params({"command": "x", "env": {"K": "${NOPE_NOT_SET}"}})
        assert p.env["K"] == ""

    def test_a_config_with_no_placeholders_is_unchanged(self, monkeypatch):
        from agent import mcp_client
        p = mcp_client._params({"command": "npx", "args": ["-y", "plain"],
                                "env": {"MODE": "fast"}})
        assert p.args == ["-y", "plain"] and p.env["MODE"] == "fast"
