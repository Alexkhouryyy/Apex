"""One list of tables, shared by both entry points.

`main.py` and `app/resident.py` each carried their own hand-maintained sequence
of `init_db()` calls, and they had drifted by twelve modules — all twelve
initialised interactively and none of them in resident mode, which is the
always-on daemon. `tools/wiring_audit.orphan_init_db` could not see it: it asks
whether `init_db()` is called from anywhere, and one caller satisfies that.

These tests exist because adding twelve lines would have fixed 2026-09-02 and
drifted again by 2026-11. The list is the fix; this file is what keeps it one
list.
"""
import re
from pathlib import Path

import pytest

from agent import schema

ROOT = Path(__file__).resolve().parent.parent


class TestNothingIsLeftOut:
    def test_every_module_with_an_init_db_is_in_the_list(self):
        """The check that makes the list self-maintaining. A new module with
        tables is invisible until someone lists it, and this is what says so."""
        defined = {
            p.stem for p in (ROOT / "agent").glob("*.py")
            if re.search(r"^def init_db", p.read_text(encoding="utf-8"), re.M)
        }
        missing = sorted(defined - set(schema.INIT_MODULES))
        assert not missing, (
            f"these modules create tables but nothing initialises them: {missing}. "
            f"Add them to agent/schema.INIT_MODULES.")

    def test_the_list_names_only_real_modules(self):
        """A typo would fail quietly — init_all catches the ImportError and
        moves on, so the table would simply never be created."""
        for name in schema.INIT_MODULES:
            assert (ROOT / "agent" / f"{name}.py").exists(), \
                f"agent/schema.INIT_MODULES names {name}, which does not exist"

    def test_the_extras_exist_and_are_callable(self):
        import importlib
        for mod_name, fn_name in schema.EXTRA:
            mod = importlib.import_module(f"agent.{mod_name}")
            assert callable(getattr(mod, fn_name, None)), \
                f"agent.{mod_name}.{fn_name} is listed in EXTRA but is not callable"


class TestBothEntryPointsUseIt:
    """The actual bug was two copies of one list, so the test is that there is
    now one. Asserted against the source rather than by booting both modes,
    because booting the resident daemon in a test is not something a unit test
    can honestly do — and the defect was textual in the first place."""

    @staticmethod
    def _src(rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    @pytest.mark.parametrize("entry", ["main.py", "app/resident.py"])
    def test_it_calls_init_all(self, entry):
        assert "init_all(" in self._src(entry), \
            f"{entry} does not initialise the database from the shared list"

    @pytest.mark.parametrize("entry", ["main.py", "app/resident.py"])
    def test_it_does_not_keep_its_own_list(self, entry):
        """A second list is how the first drift happened. One stray
        `X.init_db()` here is not a bug today and is the seed of the next one."""
        strays = re.findall(r"^\s*(?:\w+\.)?(\w+)\.init_db\(\)", self._src(entry), re.M)
        assert not strays, (
            f"{entry} still initialises {sorted(set(strays))} by hand. Those "
            f"belong in agent/schema.INIT_MODULES, or the two entry points "
            f"start drifting again.")


class TestBothEntryPointsIndexTheVault:
    """Same drift guard as the tables above, for the same reason.

    The vault index is kept fresh by one call at boot. One entry point having
    it and the other not is precisely the shape that left twelve modules
    uninitialised in resident mode — and it would be even quieter here, because
    a stale index does not error, it just answers with yesterday's notes.
    """

    @pytest.mark.parametrize("entry", ["main.py", "app/resident.py"])
    def test_it_starts_the_reindex(self, entry):
        src = (ROOT / entry).read_text(encoding="utf-8")
        assert "start_background_reindex(" in src, (
            f"{entry} never refreshes the vault index, so notes edited in "
            f"Obsidian stay invisible to search in that mode")


class TestItActuallyCreatesTables:
    def test_a_fresh_database_gets_the_tables(self, tmp_path, monkeypatch):
        """Not a tautology over the module list: it asserts specific tables that
        the twelve DRIFTED modules own, which is what was actually missing in
        resident mode."""
        import sqlite3
        from agent import longterm
        db = tmp_path / "fresh.db"
        monkeypatch.setattr(longterm, "DB_PATH", str(db))

        failed = schema.init_all(log=lambda *a: None)
        assert failed == [], f"init_all could not initialise: {failed}"

        with sqlite3.connect(db) as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        # One table for each of the twelve modules resident mode was missing.
        # Written out rather than derived from the module list, so a rename that
        # silently stops creating a table cannot also silently update the test.
        for table in ("board_cards", "chat_threads", "council_runs",
                      "devices", "initiative_log", "lessons", "mcp_audit",
                      "recommendation_outcomes", "interruptions",
                      "scheduled_tasks"):
            assert table in names, (
                f"'{table}' was not created — this is the exact shape of the "
                f"bug: a subsystem that boots, writes, and silently does nothing")

    def test_one_broken_module_does_not_stop_the_rest(self, tmp_path, monkeypatch):
        """A failed migration must not stop Apex booting, and must not pass
        silently either — the name has to come back to the caller."""
        import sqlite3
        from agent import longterm, budget
        db = tmp_path / "partial.db"
        monkeypatch.setattr(longterm, "DB_PATH", str(db))

        def boom():
            raise RuntimeError("simulated bad migration")
        monkeypatch.setattr(budget, "init_db", boom)

        said = []
        failed = schema.init_all(log=said.append)
        assert "budget" in failed, "a module that raised must be reported back"
        assert any("budget" in s for s in said), "and must be logged, not swallowed"

        with sqlite3.connect(db) as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "mcp_audit" in names, \
            "one broken module must not stop the modules after it"


class TestLazyGuardsArePerDatabase:
    """Five modules create their tables lazily on first use, and every one of
    them latched on a bare boolean: `if _ready: return`.

    That records THAT some database has the tables, never WHICH — so the first
    time anything pointed `longterm.DB_PATH` somewhere else, the guard went
    permanently quiet for the original database too. The guard whose entire job
    is to make a missing table impossible was disarmed by being used.

    Found the way these things get found: a `test_tui.py` case that passed on
    its own and failed in the suite with `no such table: budget_config`, sixty
    seconds and eleven hundred tests after `test_schema.py` had pointed DB_PATH
    at a temp file. Nothing about the failure named the cause.

    It is not a test-only defect. Restoring from a backup, or any runtime
    DB_PATH change, does the same thing to a running Apex.
    """

    GUARDS = [
        ("budget", "_ensure_init", "_initialized_for", "budget_config"),
        ("conversations", "_ensure_db", "_ready_for", "chat_threads"),
        ("deepresearch", "_ensure_db", "_ready_for", "research_runs"),
        ("restraint", "_ensure_db", "_ready_for", "interruptions"),
        ("threads", "_ensure_db", "_ready_for", "threads_surfaced"),
    ]

    @pytest.mark.parametrize("mod_name,guard,flag,table", GUARDS)
    def test_switching_database_re_creates_the_tables(
            self, mod_name, guard, flag, table, tmp_path, monkeypatch):
        import importlib
        import sqlite3
        from agent import longterm

        mod = importlib.import_module(f"agent.{mod_name}")
        first, second = tmp_path / "a.db", tmp_path / "b.db"

        monkeypatch.setattr(longterm, "DB_PATH", str(first))
        monkeypatch.setattr(mod, flag, None)
        getattr(mod, guard)()

        # The same guard, now against a database that has never been touched.
        monkeypatch.setattr(longterm, "DB_PATH", str(second))
        getattr(mod, guard)()

        with sqlite3.connect(second) as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert table in names, (
            f"agent/{mod_name}.{guard}() skipped the second database because it "
            f"had already run against the first — the latch remembers that it "
            f"ran, not what it ran against")

    @pytest.mark.parametrize("mod_name,guard,flag,table", GUARDS)
    def test_it_still_does_not_re_run_for_the_same_database(
            self, mod_name, guard, flag, table, tmp_path, monkeypatch):
        """The other half. A guard that re-initialises on every call would pass
        the test above and quietly put a CREATE TABLE on every hot path."""
        import importlib
        from agent import longterm

        mod = importlib.import_module(f"agent.{mod_name}")
        monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "one.db"))
        monkeypatch.setattr(mod, flag, None)
        getattr(mod, guard)()

        calls = []
        real_init = mod.init_db
        monkeypatch.setattr(mod, "init_db", lambda: calls.append(1) or real_init())
        getattr(mod, guard)()
        assert calls == [], (
            f"agent/{mod_name}.{guard}() re-initialises on every call — the "
            f"latch is not latching")
