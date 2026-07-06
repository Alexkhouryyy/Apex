"""BUG 3 regression: approving a staged confirm-tier cortex action must actually
run it (previously cortex.approve_action returned "[cortex] no executor" and did
nothing for bash/write_file/send_email/etc.)."""
import json
import sys
import time
import types

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
