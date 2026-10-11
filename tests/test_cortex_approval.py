"""BUG 3 regression: approving a staged confirm-tier cortex action must actually
run it (previously cortex.approve_action returned "[cortex] no executor" and did
nothing for bash/write_file/send_email/etc.)."""
import json
import sys
import time
import types
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event

import pytest

from agent import cortex, longterm


def test_execute_tool_delegates_confirm_tools_to_real_dispatcher(monkeypatch):
    seen = {}
    stub = types.ModuleType("agent.core")
    stub._execute_tool = lambda name, inputs: (seen.update(name=name, inputs=inputs) or f"ran {name}")
    monkeypatch.setitem(sys.modules, "agent.core", stub)

    out = cortex._execute_tool("bash", {"command": "echo hi"})
    assert out == "ran bash"
    assert seen["name"] == "bash"
    assert seen["inputs"] == {"command": "echo hi"}


def test_read_only_tools_do_not_delegate(monkeypatch):
    # 'always' tools must keep running through cortex's own sandboxed handlers,
    # never the real dispatcher.
    stub = types.ModuleType("agent.core")
    stub._execute_tool = lambda name, inputs: pytest_fail_marker()
    monkeypatch.setitem(sys.modules, "agent.core", stub)

    from tools import sandbox
    # Origin-based sandbox: autonomous run_python goes through the Docker path.
    # Simulate Docker-available with a Local backend so the code executes; the point
    # of this test is that run_python does NOT fall through to the real dispatcher.
    monkeypatch.setattr(sandbox, "autonomous_backend", lambda refresh=False: sandbox.LocalBackend())
    out = cortex._execute_tool("run_python", {"code": "print(2+2)"})
    assert "4" in out  # ran in the (simulated) sandbox, did not hit the stub dispatcher


def pytest_fail_marker():
    raise AssertionError("read-only tool incorrectly delegated to real dispatcher")


def test_approve_action_runs_and_marks_approved(test_db, monkeypatch):
    cortex.init_db()
    seen = {}
    stub = types.ModuleType("agent.core")
    stub._execute_tool = lambda name, inputs: (seen.update(name=name) or f"ran {name}")
    monkeypatch.setitem(sys.modules, "agent.core", stub)

    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO pending_actions (ts, goal_id, tool, inputs_json, rationale) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time(), None, "bash", json.dumps({"command": "echo hi"}), "test"),
        )
        action_id = cur.lastrowid

    result = cortex.approve_action(action_id)
    assert "ran bash" in result
    assert seen["name"] == "bash"

    with longterm._conn() as c:
        status = c.execute(
            "SELECT status FROM pending_actions WHERE id = ?", (action_id,)
        ).fetchone()[0]
    assert status == "approved"


def stage_action(*, inputs='{"command": "echo test"}', status="pending", goal_id=None):
    cortex.init_db()
    with longterm._conn() as c:
        cur = c.execute(
            "INSERT INTO pending_actions (ts, goal_id, tool, inputs_json, rationale, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), goal_id, "bash", inputs, "test", status),
        )
        return cur.lastrowid


def action_status(action_id):
    with longterm._conn() as c:
        return c.execute("SELECT status FROM pending_actions WHERE id = ?", (action_id,)).fetchone()[0]


def test_simultaneous_approvals_dispatch_once_and_cannot_be_rejected(test_db, monkeypatch):
    action_id = stage_action(goal_id=42)
    start = Barrier(2)
    executing, release = Event(), Event()
    calls, progress = [], []

    def execute(tool, inputs, **kwargs):
        calls.append((tool, inputs))
        assert action_status(action_id) == "running", "claim must commit before dispatch"
        executing.set()
        assert release.wait(5), "test failed to release executor"
        return "ran once"

    def approve():
        start.wait(timeout=5)
        return cortex.approve_action(action_id)

    monkeypatch.setattr(cortex, "_execute_tool", execute)
    monkeypatch.setattr(cortex.goals_mod, "update_goal", lambda *a, **k: progress.append((a, k)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve) for _ in range(2)]
        try:
            assert executing.wait(5)
            done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            assert len(done) == 1, "a duplicate approval must return while the first is running"
            assert "already processed" in next(iter(done)).result()
            assert "already processed" in cortex.reject_action(action_id)
            assert action_status(action_id) == "running"
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert results.count("ran once") == 1
    assert len(calls) == len(progress) == 1
    assert action_status(action_id) == "approved"
    assert "already processed" in cortex.approve_action(action_id)
    assert "already processed" in cortex.reject_action(action_id)
    assert action_status(action_id) == "approved"


def test_uncertain_execution_is_not_repeated(test_db, monkeypatch, tmp_path):
    action_id = stage_action()
    marker = tmp_path / "effect.txt"

    def effect_then_error(tool, inputs, **kwargs):
        with marker.open("a") as f:
            f.write("effect\n")
        raise RuntimeError("connection lost after the effect")

    monkeypatch.setattr(cortex, "_execute_tool", effect_then_error)
    with pytest.raises(RuntimeError, match="after the effect"):
        cortex.approve_action(action_id)
    assert action_status(action_id) == "unknown"
    assert "already processed" in cortex.approve_action(action_id)
    assert "already processed" in cortex.reject_action(action_id)
    assert marker.read_text() == "effect\n"
    assert cortex.list_pending("unknown")[0]["id"] == action_id


def test_real_cortex_wrapper_preserves_dispatcher_errors(test_db, monkeypatch, tmp_path):
    action_id = stage_action()
    marker = tmp_path / "dispatched.txt"
    stub = types.ModuleType("agent.core")
    def effect_then_error(tool, inputs):
        with marker.open("a") as f:
            f.write("effect\n")
        raise RuntimeError("lost completion receipt")
    stub._execute_tool = effect_then_error
    monkeypatch.setitem(sys.modules, "agent.core", stub)
    with pytest.raises(RuntimeError, match="completion receipt"):
        cortex.approve_action(action_id)
    assert action_status(action_id) == "unknown"
    assert "already processed" in cortex.approve_action(action_id)
    assert marker.read_text() == "effect\n"


def test_different_actions_can_execute_while_one_is_running(test_db, monkeypatch):
    first, second = stage_action(), stage_action()
    executing, release = Event(), Event()

    def execute(tool, inputs, **kwargs):
        if not executing.is_set():
            executing.set()
            assert release.wait(5)
        return "ran"

    monkeypatch.setattr(cortex, "_execute_tool", execute)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_result = pool.submit(cortex.approve_action, first)
        try:
            assert executing.wait(5)
            assert cortex.approve_action(second) == "ran"
            assert action_status(first) == "running"
            assert action_status(second) == "approved"
        finally:
            release.set()
        assert first_result.result(timeout=5) == "ran"


@pytest.mark.parametrize("status", ["running", "unknown", "failed", "approved", "rejected"])
def test_existing_claims_survive_database_initialization(test_db, monkeypatch, status):
    action_id = stage_action(status=status)
    cortex.init_db()
    def unexpected_execution(*args):
        pytest.fail("a non-pending action must never execute again")
    monkeypatch.setattr(cortex, "_execute_tool", unexpected_execution)
    assert "already processed" in cortex.approve_action(action_id)
    assert "already processed" in cortex.reject_action(action_id)
    assert action_status(action_id) == status


@pytest.mark.parametrize("inputs", ["{bad-json", "[]", "null"])
def test_invalid_inputs_fail_before_dispatch(test_db, monkeypatch, inputs):
    action_id = stage_action(inputs=inputs)
    monkeypatch.setattr(cortex, "_execute_tool", lambda *a, **k: pytest.fail("invalid inputs were dispatched"))
    with pytest.raises(ValueError):
        cortex.approve_action(action_id)
    assert action_status(action_id) == "failed"
    assert "already processed" in cortex.approve_action(action_id)


def test_rejected_action_cannot_execute(test_db, monkeypatch):
    action_id = stage_action()
    monkeypatch.setattr(cortex, "_execute_tool", lambda *a, **k: pytest.fail("rejected action ran"))
    assert cortex.reject_action(action_id) == f"Action #{action_id} rejected."
    assert "already processed" in cortex.approve_action(action_id)
    assert action_status(action_id) == "rejected"


def test_status_filter_exposes_uncertain_actions(test_db, monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import server
    import config

    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "test-cortex-owner")
    server._throttle.reset("testclient")
    action_id = stage_action(status="unknown")
    client = TestClient(server.app, headers={"Authorization": "Bearer test-cortex-owner"})
    assert client.get("/api/pending-actions").json() == {"actions": []}
    result = client.get("/api/pending-actions?status=unknown")
    assert result.status_code == 200
    assert result.json()["actions"][0]["id"] == action_id
