"""Operating Apex from its own dashboard.

These endpoints rewrite credentials, restart the process and pull new code into
it. That makes almost everything worth testing a REFUSAL, not a success: the
happy paths are one line each and the interesting question is always what
happens when they should not run.

The specific failure this file exists to prevent is the one the board just had —
a control surface that looks present and is either unreachable or, far worse,
reachable by the wrong person.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from agent import control  # noqa: E402
import set_env_key  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A .env of our own, so no test can touch the real one."""
    p = tmp_path / ".env"
    p.write_text("DASHBOARD_TOKEN=realsecret\nANTHROPIC_API_KEY=sk-ant-aaaaaaaaaaaa\n")
    monkeypatch.setattr(control, "env_path", lambda: p)
    return p


class TestEnvWriteRefusals:
    """`scripts/set_env_key.py` used to be reachable only from a command line.
    It is now reachable over HTTP, which changes what counts as a bad value."""

    def test_a_newline_cannot_smuggle_a_second_assignment(self, tmp_path):
        """THE injection. This file is written as `"\\n".join(lines)`, so a value
        containing a line break does not become a multi-line value — it becomes
        a second assignment, of any variable the caller names.

        Before the guard this produced a .env ending in the attacker's
        DASHBOARD_TOKEN. Remove the check and this test fails.
        """
        p = tmp_path / ".env"
        p.write_text("DASHBOARD_TOKEN=realsecret\n")
        with pytest.raises(set_env_key.EnvWriteRefused):
            set_env_key.set_key(p, "OLLAMA_MODEL", "x\nDASHBOARD_TOKEN=attacker")
        assert "attacker" not in p.read_text()

    def test_a_carriage_return_is_refused_too(self, tmp_path):
        """Apex runs on Windows, where the line ending is \\r\\n — checking only
        \\n would leave the platform-native half of the attack open."""
        p = tmp_path / ".env"
        p.write_text("A=1\n")
        with pytest.raises(set_env_key.EnvWriteRefused):
            set_env_key.set_key(p, "A", "x\rDASHBOARD_TOKEN=attacker")

    @pytest.mark.parametrize("bad", ["", "  ", "9LIVES", "A B", "A-B", "A=B", "A;rm"])
    def test_a_key_that_is_not_an_identifier_is_refused(self, tmp_path, bad):
        p = tmp_path / ".env"
        p.write_text("A=1\n")
        with pytest.raises(set_env_key.EnvWriteRefused):
            set_env_key.set_key(p, bad, "value")

    def test_an_ordinary_value_still_writes(self, tmp_path):
        """The guards must not have made the thing unusable."""
        p = tmp_path / ".env"
        p.write_text("A=1\n")
        assert set_env_key.set_key(p, "B", "hello world") == "added"
        assert "B=hello world" in p.read_text()


class TestSecretsNeverLeave:
    def test_a_secret_value_is_never_returned(self, env):
        """A dashboard that renders your API key into the DOM has published it
        to every browser extension on the page and every screenshot you take."""
        rows = {r["key"]: r for r in control.entries()}
        row = rows["ANTHROPIC_API_KEY"]
        assert row["set"] is True
        assert "sk-ant-aaaaaaaaaaaa" not in row["display"], "the key was sent to the browser"
        assert "chars" in row["display"], "but it must still show that it is set"

    def test_a_whole_settings_payload_contains_no_secret(self, env):
        """Belt and braces across every key at once, so a newly-added secret
        cannot slip through by not being named in a test."""
        blob = repr(control.entries())
        assert "sk-ant-aaaaaaaaaaaa" not in blob
        assert "realsecret" not in blob

    @pytest.mark.parametrize("key", [
        "ANTHROPIC_API_KEY", "DASHBOARD_TOKEN", "TELEGRAM_BOT_TOKEN",
        "VAPID_PRIVATE_KEY", "TWILIO_AUTH_TOKEN", "OPENAI_API_KEY",
    ])
    def test_the_things_that_must_be_masked_are_masked(self, key):
        assert control.is_secret(key), f"{key} would have been shown in the clear"

    def test_an_ordinary_setting_is_shown_in_full(self):
        """Masking everything would be safe and useless — you could not read
        your own model name or port back."""
        assert not control.is_secret("BOARD_FPS")
        assert control.describe_value("BOARD_FPS", "15") == "15"


class TestSettingRefusals:
    def test_a_key_config_does_not_read_is_refused(self, env):
        """The signature bug of this codebase, with a text box in front of it.

        `ANTHROPIC_KEY` (no `_API_`) would write cleanly, display correctly, and
        do nothing at all — indistinguishable from a working setting.
        """
        ok, msg = control.set_setting("ANTHROPIC_KEY", "sk-whatever")
        assert not ok
        assert "does not read" in msg and "ANTHROPIC_KEY" in msg

    def test_the_dashboard_token_cannot_be_changed_from_the_dashboard(self, env):
        """It is the credential authorizing the request making the change. A
        typo locks you out of the only surface that could fix it."""
        ok, msg = control.set_setting("DASHBOARD_TOKEN", "newtoken")
        assert not ok and "lock you out" in msg
        assert "newtoken" not in env.read_text()

    def test_the_vapid_private_key_cannot_be_changed_from_the_dashboard(self, env):
        """It cannot be regenerated without silently unsubscribing every device
        that ever enabled push."""
        ok, msg = control.set_setting("VAPID_PRIVATE_KEY", "nope")
        assert not ok
        assert "nope" not in env.read_text()

    def test_a_real_setting_writes_and_says_it_needs_a_restart(self, env):
        ok, msg = control.set_setting("BOARD_FPS", "24")
        assert ok, msg
        assert "BOARD_FPS=24" in env.read_text()
        assert "restart" in msg.lower(), \
            "nothing re-reads .env while running; saying only 'saved' implies it took effect"

    def test_an_injection_through_the_setting_layer_is_refused(self, env):
        """The same attack as above, arriving through the API's own entry point
        rather than the writer's — no exception, a clean refusal."""
        ok, msg = control.set_setting("BOARD_FPS", "24\nDASHBOARD_TOKEN=attacker")
        assert not ok
        assert "attacker" not in env.read_text()


class TestRestartRefusesWithoutASupervisor:
    def test_no_supervisor_means_no_restart(self, monkeypatch):
        """Exiting is easy; coming back is not. Without a supervisor this is
        just Quit with a friendlier label, and you would be left with a dead
        console and no dashboard to fix it from."""
        monkeypatch.delenv(control.SUPERVISOR_ENV, raising=False)
        assert control.restart_status()["ok"] is False
        fired = []
        control.set_restart_hook(lambda: fired.append(1))
        try:
            ok, msg = control.request_restart(delay=0)
            assert not ok
            assert "Apex.bat" in msg
        finally:
            control.set_restart_hook(None)
        assert fired == [], "it exited anyway"

    def test_a_supervisor_makes_it_available(self, monkeypatch):
        import time
        monkeypatch.setenv(control.SUPERVISOR_ENV, "1")
        assert control.restart_status()["ok"] is True
        fired = []
        control.set_restart_hook(lambda: fired.append(1))
        try:
            ok, _ = control.request_restart(delay=0)
            assert ok
            for _ in range(100):
                if fired:
                    break
                time.sleep(0.01)
        finally:
            control.set_restart_hook(None)
        assert fired == [1], "the restart was reported but never happened"

    def test_the_supervisor_restarts_only_on_the_agreed_code(self):
        """A crash is not a restart request. Respawning one loops forever while
        looking, from outside, exactly like Apex running fine."""
        from scripts.apex_supervisor import should_restart
        assert should_restart(control.EXIT_RESTART)
        for other in (0, 1, 2, 43, 130, 255, -1):
            assert not should_restart(other), f"exit {other} must not respawn"


class TestUpdateReportsFourStates:
    def test_a_dirty_tree_refuses_rather_than_merging_over_your_work(self, monkeypatch):
        monkeypatch.setattr(control, "_git", lambda *a, **k: (
            (0, "main") if a[0] == "rev-parse" else (0, " M agent/core.py")))
        st = control.update_status()
        assert st["state"] == "dirty" and st["can_update"] is False
        assert "overwrite" in st["detail"]

    def test_a_clean_tree_is_ready(self, monkeypatch):
        monkeypatch.setattr(control, "_git", lambda *a, **k: (
            (0, "main") if a[0] == "rev-parse" else (0, "")))
        st = control.update_status()
        assert st["state"] == "ready" and st["can_update"] is True

    def test_not_a_checkout_is_its_own_answer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(control, "repo_root", lambda: tmp_path)
        st = control.update_status()
        assert st["state"] == "not_a_repo" and st["can_update"] is False

    def test_a_pull_that_fetched_nothing_does_not_claim_it_updated(self, monkeypatch):
        """The four-way honesty rule, applied to git.

        A pull that changed no commits reporting "updated — restart now" is the
        same lie as a board command returning 204 with no stage open: you would
        restart for nothing and believe you were current when you were not.
        """
        monkeypatch.setattr(control, "update_status",
                            lambda: {"state": "ready", "branch": "main", "can_update": True})
        monkeypatch.setattr(control, "_git", lambda *a, **k: (0, "abc123")
                            if a[0] == "rev-parse" else (0, "Already up to date."))
        r = control.do_update()
        assert r["ok"] and r["changed"] is False
        assert r["state"] == "already_current"

    def test_a_pull_that_landed_commits_says_how_many(self, monkeypatch):
        seq = {"rev-parse": ["old", "new"], "pull": ["ok"],
               "log": ["a1 one\na2 two"]}
        def fake(*a, **k):
            return 0, seq[a[0]].pop(0)
        monkeypatch.setattr(control, "update_status",
                            lambda: {"state": "ready", "branch": "main", "can_update": True})
        monkeypatch.setattr(control, "_git", fake)
        r = control.do_update()
        assert r["changed"] is True and r["count"] == 2

    def test_a_failed_pull_is_not_reported_as_success(self, monkeypatch):
        monkeypatch.setattr(control, "update_status",
                            lambda: {"state": "ready", "branch": "main", "can_update": True})
        monkeypatch.setattr(control, "_git", lambda *a, **k:
                            (0, "old") if a[0] == "rev-parse" else (1, "network unreachable"))
        r = control.do_update()
        assert r["ok"] is False and "network unreachable" in r["detail"]


class TestMcpVisibility:
    def test_never_having_run_is_not_the_same_as_having_no_servers(self, monkeypatch):
        """One boolean would flatten these into "no MCP", and they need
        different fixes: one is a wiring bug, the other is a config file."""
        from agent import mcp_client
        monkeypatch.setattr(mcp_client, "_ran", False, raising=False)
        assert mcp_client.status()["state"] == "never_ran"
        monkeypatch.setattr(mcp_client, "_ran", True, raising=False)
        monkeypatch.setattr(mcp_client, "_status", {}, raising=False)
        assert mcp_client.status()["state"] == "no_config"

    def test_a_server_that_failed_keeps_its_error(self, monkeypatch):
        """It printed one line at boot and then vanished. A missing tool looks
        exactly like a tool the model chose not to call."""
        from agent import mcp_client
        monkeypatch.setattr(mcp_client, "_ran", True, raising=False)
        monkeypatch.setattr(mcp_client, "_status", {
            "slack": {"server": "slack", "state": "connected", "tools": 3,
                      "tool_names": [], "command": "npx", "source": "s", "error": ""},
            "notion": {"server": "notion", "state": "failed", "tools": 0,
                       "tool_names": [], "command": "npx", "source": "s",
                       "error": "FileNotFoundError: npx"},
        }, raising=False)
        st = mcp_client.status()
        assert st["state"] == "degraded"
        assert "1 of 2" in st["detail"]
        failed = [s for s in st["servers"] if s["state"] == "failed"][0]
        assert "npx" in failed["error"]

    def test_all_connected_is_ok(self, monkeypatch):
        from agent import mcp_client
        monkeypatch.setattr(mcp_client, "_ran", True, raising=False)
        monkeypatch.setattr(mcp_client, "_status", {
            "slack": {"server": "slack", "state": "connected", "tools": 3,
                      "tool_names": [], "command": "", "source": "", "error": ""},
        }, raising=False)
        assert mcp_client.status()["state"] == "ok"


class TestControlEndpointsRequireTheMasterToken:
    """A paired device may USE Apex. It may not rewrite its credentials,
    restart it, or pull new code into it.

    Run with a real token, because `DASHBOARD_TOKEN=""` disables the middleware
    and would test nothing — the exact reason /board shipped returning 401.
    """

    TOKEN = "control-master-token"
    PATHS = ["/api/control/settings", "/api/control/update", "/api/control/mcp"]

    def _client(self, monkeypatch):
        from fastapi.testclient import TestClient
        import config
        from dashboard import server
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", self.TOKEN, raising=False)
        server._throttle.reset("testclient")
        return TestClient(server.app)

    def _master(self):
        return {"Authorization": f"Bearer {self.TOKEN}"}

    def test_no_token_gets_nothing(self, monkeypatch):
        c = self._client(monkeypatch)
        for p in self.PATHS:
            assert c.get(p).status_code in (401, 429), p

    def test_a_device_token_is_refused_with_a_reason(self, monkeypatch):
        """403 and an explanation, not a blank page — the user needs to know it
        is the wrong credential rather than a broken server."""
        from dashboard import server
        from agent import access_tokens
        c = self._client(monkeypatch)
        monkeypatch.setattr(access_tokens, "verify", lambda t: t == "device-token")
        h = {"Authorization": "Bearer device-token"}
        for p in self.PATHS:
            r = c.get(p, headers=h)
            assert r.status_code == 403, p
            assert "master" in r.json().get("error", "").lower(), p

    def test_a_device_token_cannot_restart_apex(self, monkeypatch):
        from agent import access_tokens
        c = self._client(monkeypatch)
        monkeypatch.setattr(access_tokens, "verify", lambda t: t == "device-token")
        r = c.post("/api/control/restart",
                   headers={"Authorization": "Bearer device-token"})
        assert r.status_code == 403

    def test_a_device_token_cannot_write_a_setting(self, monkeypatch):
        from agent import access_tokens
        c = self._client(monkeypatch)
        monkeypatch.setattr(access_tokens, "verify", lambda t: t == "device-token")
        r = c.post("/api/control/settings",
                   headers={"Authorization": "Bearer device-token"},
                   json={"key": "BOARD_FPS", "value": "1"})
        assert r.status_code == 403

    def test_the_master_token_gets_through(self, monkeypatch):
        c = self._client(monkeypatch)
        r = c.get("/api/control/settings", headers=self._master())
        assert r.status_code == 200
        assert isinstance(r.json().get("settings"), list)

    def test_the_settings_endpoint_ships_no_secret_over_http(self, monkeypatch):
        """The masking rule, enforced at the boundary it actually matters at."""
        import config
        c = self._client(monkeypatch)
        body = c.get("/api/control/settings", headers=self._master()).text
        assert self.TOKEN not in body, "the dashboard token came back over HTTP"
        real = getattr(config, "ANTHROPIC_API_KEY", "") or ""
        if len(real) > 12:
            assert real not in body


class TestThemes:
    """A theme in the dropdown with no palette behind it renders as the default
    and looks exactly like the click did nothing."""

    def _css(self):
        return Path("dashboard/static/styles.css").read_text(encoding="utf-8")

    def _js(self):
        return Path("dashboard/static/app.js").read_text(encoding="utf-8")

    def _theme_ids(self):
        import re
        js = self._js()
        block = js[js.index("const THEMES = ["):]
        block = block[:block.index("];")]
        return re.findall(r"id:\s*'([a-z]+)'", block)

    def test_every_theme_offered_has_a_palette(self):
        css = self._css()
        for tid in self._theme_ids():
            assert f'[data-theme="{tid}"]' in css, \
                f"'{tid}' is in the dropdown with no CSS behind it"

    def test_no_theme_is_missing_a_variable(self):
        """A theme that omits one variable inherits the default's value for it —
        which is how you get dark grey text on a light background, in exactly
        one theme, on exactly the widgets nobody clicked."""
        import re
        css = self._css()
        blocks = dict(re.findall(
            r':root\[data-theme="([a-z]+)"\]\s*\{([^}]*)\}', css, re.S))
        base = re.search(r':root,\s*\n:root\[data-theme="midnight"\]\s*\{([^}]*)\}',
                         css, re.S)
        assert base, "the default palette block moved; this test cannot check anything"
        expected = set(re.findall(r"(--[a-z0-9-]+)\s*:", base.group(1)))
        assert len(expected) > 10
        for name, body in blocks.items():
            if name == "midnight":
                continue
            got = set(re.findall(r"(--[a-z0-9-]+)\s*:", body))
            assert not (expected - got), \
                f"theme '{name}' is missing {sorted(expected - got)}"

    def test_every_colour_is_a_colour(self):
        """`--text-dim: #5d7align;` was a real typo in the first draft. CSS
        silently drops an invalid value and falls back, so it renders as
        'almost right' rather than as an error."""
        import re
        css = self._css()
        bad = []
        for name, value in re.findall(
                r"(--(?:bg|text|accent|border|success|warn|danger)[a-z0-9-]*)\s*:\s*([^;]+);", css):
            v = value.strip()
            if v.startswith("#") and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
                bad.append(f"{name}: {v}")
        assert not bad, f"invalid colour values: {bad}"


class TestEveryTabIsWired:
    """Nav button ↔ pane ↔ loader, for every tab in the dashboard.

    Written while adding Control, but deliberately general, because all three
    ways of getting this wrong are silent:

      * a nav button whose pane does not exist — clicking it throws inside the
        click handler and the page simply stops responding to the sidebar;
      * a pane with no button — the markup is there and unreachable, which is
        this project's most-repeated bug in its purest form;
      * a `loadTab` entry naming a function that does not exist — the tab opens,
        stays empty, and the ReferenceError goes to a console nobody has open.

    Only the last one needed a new mistake to be possible. The other two were
    already possible and untested for 26 tabs.
    """

    def _html(self):
        return Path("dashboard/static/index.html").read_text(encoding="utf-8")

    def _js(self):
        return Path("dashboard/static/app.js").read_text(encoding="utf-8")

    def _nav_tabs(self):
        import re
        return set(re.findall(r'<button data-tab="([a-z-]+)"', self._html()))

    def _panes(self):
        import re
        return set(re.findall(r'<section id="tab-([a-z-]+)"', self._html()))

    def test_every_nav_button_has_a_pane(self):
        missing = self._nav_tabs() - self._panes()
        assert not missing, f"nav buttons with no pane: {sorted(missing)}"

    def test_every_pane_has_a_nav_button(self):
        orphan = self._panes() - self._nav_tabs()
        assert not orphan, f"panes nothing can open: {sorted(orphan)}"

    def test_the_control_tab_is_actually_reachable(self):
        """Named explicitly so a regression points at the right feature."""
        assert "control" in self._nav_tabs()
        assert "control" in self._panes()

    def test_every_loader_named_in_loadTab_exists(self):
        """`control: loadControl` with no `loadControl` defined throws the
        moment the tab is clicked, and the tab just sits there empty."""
        import re
        js = self._js()
        block = js[js.index("async function loadTab(tab) {"):]
        block = block[:block.index("  };")]
        loaders = dict(re.findall(r"^\s*([a-z]+):\s*([A-Za-z_$][\w$]*),", block, re.M))
        assert len(loaders) > 20, "the loadTab map moved; this test checks nothing"
        for tab, fn in loaders.items():
            assert re.search(rf"(async\s+)?function\s+{re.escape(fn)}\s*\(", js), \
                f"loadTab maps '{tab}' to {fn}(), which is not defined anywhere"

    def test_every_tab_named_in_loadTab_is_a_real_tab(self):
        import re
        js = self._js()
        block = js[js.index("async function loadTab(tab) {"):]
        block = block[:block.index("  };")]
        named = set(re.findall(r"^\s*([a-z]+):", block, re.M))
        unknown = named - self._nav_tabs()
        assert not unknown, f"loadTab loads tabs that do not exist: {sorted(unknown)}"
