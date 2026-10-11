"""Exercise cortex dispatch through the real research search, with offline backends."""
import json
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

import config
from agent import budget, cortex, goals, longterm
from tools import research


@pytest.fixture(autouse=True)
def isolated_cortex(test_db, monkeypatch):
    goals.init_db()
    cortex.init_db()
    budget.init_db()
    monkeypatch.setattr(cortex, "_last_tick", 0)
    monkeypatch.setattr(cortex, "_notify_fn", None)
    # Exercise the real search router without any paid call or network request.
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")


def backend(monkeypatch, rows=(), error=None):
    calls = []

    class FakeDDGS:
        def text(self, query, max_results):
            calls.append((query, max_results))
            if error is not None:
                raise error
            return list(rows)

    module = ModuleType("ddgs")
    module.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", module)
    return calls


def decision_client(monkeypatch, tool):
    goals.set_goal("Research battery storage", "", horizon="week")
    goal_id = goals.list_goals()[0]["id"]
    decision = {"goal_id": goal_id, "tool": tool,
                "inputs": {"query": "battery storage"}, "rationale": "Find current sources"}
    # Only the decision LLM is mocked; dispatch and search routing remain real.
    monkeypatch.setattr(cortex.telemetry, "create", lambda *a, **kw:
                        NS(content=[NS(text=json.dumps(decision))]))
    return goal_id


def progress():
    with longterm._conn() as c:
        return c.execute("SELECT note FROM goal_progress ORDER BY id").fetchall()


@pytest.mark.parametrize("tool", ["search_web", "query_web"])
def test_dispatch_reaches_existing_search_and_caps_results(monkeypatch, tool):
    calls = backend(monkeypatch, rows=[{"title": "Storage source", "href": "https://example.test/storage", "body": "Measured storage data"}])
    result = cortex._execute_tool(tool, {"query": "battery storage"}, raise_errors=True)
    assert calls == [("battery storage", 3)]
    assert "Storage source" in result and "https://example.test/storage" in result
    assert "Measured storage data" in result and len(result) <= 500


@pytest.mark.parametrize("tool", ["search_web", "query_web"])
def test_successful_tick_records_search_output(monkeypatch, tool, capsys):
    calls = backend(monkeypatch, rows=[{"title": "Storage source", "href": "https://example.test/storage", "body": "Evidence"}])
    decision_client(monkeypatch, tool)
    result = cortex.tick(None, "", [], force=True)
    assert result["executed"] is True and "error" not in result
    assert calls == [("battery storage", 3)]
    assert len(progress()) == 1 and "Storage source" in progress()[0][0]
    assert cortex.list_pending() == []
    assert "Auto-executed" in capsys.readouterr().out


@pytest.mark.parametrize("tool", ["search_web", "query_web"])
def test_failed_tick_is_not_goal_progress(monkeypatch, tool, capsys):
    calls = backend(monkeypatch, error=RuntimeError("search service unavailable"))
    goal_id = decision_client(monkeypatch, tool)
    before = goals.list_goals()[0]["updated_at"]
    result = cortex.tick(None, "", [], force=True)
    assert result["executed"] is False
    assert "all search backends failed" in result["error"]
    assert "search service unavailable" in result["result"]
    assert result["goal_id"] == goal_id and calls == [("battery storage", 3)]
    assert progress() == [] and cortex.list_pending() == []
    assert goals.list_goals()[0]["updated_at"] == before
    output = capsys.readouterr().out
    assert "Auto-execution failed" in output and "Auto-executed" not in output


@pytest.mark.parametrize("tool", ["search_web", "query_web"])
def test_no_matches_is_a_completed_search_with_explicit_empty_result(monkeypatch, tool):
    backend(monkeypatch)
    decision_client(monkeypatch, tool)
    result = cortex.tick(None, "", [], force=True)
    assert result["executed"] is True and "error" not in result
    assert result["result"] == "No web results found."
    assert progress() == [("[auto] No web results found.",)]


def test_real_search_backend_fallback_still_runs(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "placeholder-test-key")
    monkeypatch.setattr(config, "BACKGROUND_MODEL", "claude-haiku-4-5")
    def primary_failure(*args):
        raise RuntimeError("primary backend unavailable")
    monkeypatch.setattr(research, "_search_via_anthropic", primary_failure)
    calls = backend(monkeypatch, rows=[{"title": "Fallback source", "href": "https://example.test/fallback"}])
    assert "Fallback source" in cortex._execute_tool("search_web", {"query": "battery storage"}, raise_errors=True)
    assert calls == [("battery storage", 3)]


def test_default_dispatch_preserves_readable_error_and_strict_dispatch_raises(monkeypatch):
    backend(monkeypatch, error=RuntimeError("backend offline"))
    assert cortex._execute_tool("search_web", {"query": "test"}).startswith("[cortex] search_web failed:")
    with pytest.raises(research.SearchError, match="backend offline"):
        cortex._execute_tool("search_web", {"query": "test"}, raise_errors=True)
