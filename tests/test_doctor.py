"""The one tool written for the person RUNNING Apex, not building it.

Apex has 2000 tests, a boot smoke check and three static audits, and every one
of them runs where the code is developed. None ran on the laptop — so a missing
numpy during setup, a credit balance that made every feature look dead, and a
relay that never started because a key was never written were all found one at
a time, by a person, in whatever order they tripped over them.

The rule these tests hold: a check may only report what it OBSERVED.
"Configured" is never reported as "working".
"""
import pytest

import config
from tools import doctor


class TestConfiguredIsNotWorking:
    """The distinction this codebase keeps rediscovering. A doctor that read
    settings and pronounced them healthy would be the fail-open shape in its
    purest form — an instrument that agrees with you."""

    def test_the_model_check_makes_a_real_call(self, monkeypatch):
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-looks-real-enough")
        called = []

        class FakeClient:
            def __init__(self, api_key=None): pass
            class messages:
                @staticmethod
                def create(**kw):
                    called.append(kw)
                    return None
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", lambda api_key=None: FakeClient())
        monkeypatch.setattr(FakeClient, "messages", FakeClient.messages)
        state, _ = doctor._model()
        assert called, "the model check passed without calling anything"
        assert state == doctor.OK

    def test_a_plausible_key_that_fails_is_reported_as_broken(self, monkeypatch):
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
        import anthropic

        def boom(api_key=None):
            raise RuntimeError("authentication_error: invalid x-api-key")
        monkeypatch.setattr(anthropic, "Anthropic", boom)
        state, detail = doctor._model()
        assert state == doctor.BAD

    def test_the_credit_message_names_the_way_out(self, monkeypatch):
        """The exact failure a real laptop hit. It made every feature look
        broken, and the fix is one command."""
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        import anthropic

        def boom(api_key=None):
            raise RuntimeError("Your credit balance is too low to access the "
                               "Anthropic API.")
        monkeypatch.setattr(anthropic, "Anthropic", boom)
        state, detail = doctor._model()
        assert state == doctor.BAD
        assert "SUBSCRIPTION_ENABLED true" in detail
        assert "cannot answer anything at all" in detail

    def test_a_missing_key_is_caught_before_any_call(self, monkeypatch):
        monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", False, raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import anthropic

        def _must_not_run(api_key=None):
            raise AssertionError("called the API with no key")
        monkeypatch.setattr(anthropic, "Anthropic", _must_not_run)
        assert doctor._model()[0] == doctor.BAD


class TestEveryFailureSaysWhatToRun:
    """A diagnostic that reports a problem and not its remedy makes the person
    go and ask someone. That is the thing this was built to stop."""

    @pytest.mark.parametrize("fn", [doctor._tables, doctor._relay,
                                    doctor._dashboard, doctor._deps])
    def test_a_failure_carries_an_arrow(self, fn, monkeypatch, tmp_path):
        """The database is CREATED but left empty, deliberately.

        The first version pointed at a path that did not exist, so `_tables`
        took its "no database yet" branch and the missing-tables branch — the
        one this test is actually about — was never reached. Stripping the
        remedy from it still passed. A real, empty file is what forces the
        branch that lists what is missing.
        """
        import sqlite3
        from agent import longterm
        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()
        monkeypatch.setattr(longterm, "DB_PATH", str(db))
        monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "RELAY_URL", "http://127.0.0.1:1", raising=False)
        monkeypatch.setattr(config, "RELAY_KEY", "", raising=False)
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        state, detail = fn()
        if state == doctor.OK:
            return
        assert "->" in detail, f"{fn.__name__} reported a problem with no remedy"


class TestSpecificDiagnoses:
    def test_an_empty_dashboard_token_is_a_failure_not_a_warning(self, monkeypatch):
        """It does not merely leave the dashboard open — it turns the auth
        middleware OFF entirely, which is how a real /board bug stayed hidden."""
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "", raising=False)
        state, detail = doctor._dashboard()
        assert state == doctor.BAD and "turns OFF authentication" in detail

    def test_the_old_pinch_guess_is_flagged(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.45, raising=False)
        state, detail = doctor._pinch()
        assert state == doctor.BAD and "calibrate_pinch" in detail

    def test_a_measured_pinch_threshold_passes(self, monkeypatch):
        monkeypatch.setattr(config, "HANDTRACK_PINCH_RATIO", 0.70, raising=False)
        assert doctor._pinch()[0] == doctor.OK

    def test_no_mcp_servers_explains_the_underscore_trap(self, monkeypatch):
        """Five `_example_` keys look like five servers and are zero."""
        from agent import mcp_catalog
        monkeypatch.setattr(mcp_catalog, "installed", lambda root=None: [])
        state, detail = doctor._mcp()
        assert state == doctor.WARN and "_example_" in detail

    def test_an_uninitialised_database_is_a_warning_not_a_failure(
            self, monkeypatch, tmp_path):
        """A fresh install has no tables yet. Calling that broken would send
        someone debugging a non-problem."""
        import sqlite3
        from agent import longterm
        db = tmp_path / "fresh.db"
        sqlite3.connect(db).close()
        monkeypatch.setattr(longterm, "DB_PATH", str(db))
        state, detail = doctor._memory()
        assert state == doctor.WARN and "start Apex once" in detail


class TestItNeverCrashes:
    def test_a_check_that_raises_is_reported_not_fatal(self, monkeypatch, capsys):
        """The doctor is what you run when things are already broken. It must
        survive a subsystem being broken enough to raise on import."""
        monkeypatch.setattr(doctor, "CHECKS",
                            [(1, "explodes",
                              lambda: (_ for _ in ()).throw(RuntimeError("boom")))])
        rc = doctor.main()
        out = capsys.readouterr().out
        assert rc == 1 and "the check itself failed" in out and "boom" in out

    def test_the_exit_code_distinguishes_broken_from_unconfigured(
            self, monkeypatch, capsys):
        """Non-zero means something is STOPPING Apex. Warnings are things not
        set up yet, and conflating them would make the exit code useless."""
        monkeypatch.setattr(doctor, "CHECKS",
                            [(1, "a", lambda: (doctor.WARN, "not set up"))])
        assert doctor.main() == 0
        monkeypatch.setattr(doctor, "CHECKS",
                            [(1, "a", lambda: (doctor.BAD, "broken -> fix it"))])
        assert doctor.main() == 1
