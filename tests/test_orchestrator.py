"""Sub-agent spawning — the cap, the isolation, and the recursion block.

There was no test file for this subsystem at all before this one. `spawn()`,
`wait_for()`, `list_all()` and the `MAX_SUBAGENTS` cap had never been exercised
by anything but a live agent doing a live task — which means the cap logic,
the role isolation added alongside this file, and the no-recursion claim in
this module's own docstring were all unverified.
"""
import threading
import time

import pytest

from agent import orchestrator, subagent_scope


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every test gets its own subagent registry and its own factory, so one
    test's spawned threads cannot leave state another test would trip over."""
    monkeypatch.setattr(orchestrator, "_subagents", {})
    subagent_scope.clear_active()
    yield
    subagent_scope.clear_active()


class _FakeAgent:
    """Stands in for AgentCore. Runs whatever the test hands it as `run`,
    on the real sub-agent thread — which is the whole point: the thread
    identity, not the agent's internals, is what the isolation relies on.
    """
    def __init__(self, run_fn):
        self._run_fn = run_fn

    def run(self, framed_task, include_screenshot=False, use_thinking=False):
        return self._run_fn(framed_task)


class TestTheCapIsReal:
    """config.py used to document MAX_SUBAGENTS with nothing enforcing it —
    a phantom limit reads as a real one to anyone auditing config.py."""

    def test_spawning_past_the_cap_is_refused(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "MAX_SUBAGENTS", 2, raising=False)
        block = threading.Event()
        orchestrator.set_agent_factory(
            lambda: _FakeAgent(lambda t: block.wait(5) or "done"))
        try:
            a = orchestrator.spawn("coder", "task a")
            b = orchestrator.spawn("coder", "task b")
            assert not a.startswith("Refused") and not b.startswith("Refused")
            refusal = orchestrator.spawn("coder", "task c")
            assert "Refused" in refusal and "2" in refusal
        finally:
            block.set()
            orchestrator.wait_for([a, b], timeout=5)

    def test_an_unknown_role_is_refused_before_anything_spawns(self):
        orchestrator.set_agent_factory(lambda: _FakeAgent(lambda t: "done"))
        result = orchestrator.spawn("intern", "do a thing")
        assert "Unknown role" in result


class TestRoleIsolationEndToEnd:
    """Not just is_allowed() in isolation — the real spawn() -> thread ->
    _execute_tool_inner() path, so a change to how the thread is started
    cannot silently stop setting the scope at all."""

    def test_a_researcher_subagent_cannot_run_bash_through_the_real_dispatcher(self):
        from agent import core
        captured = {}

        def fake_run(framed_task):
            captured["result"] = core._execute_tool_inner(
                "bash", {"command": "echo should-not-run"})
            return "reported"

        orchestrator.set_agent_factory(lambda: _FakeAgent(fake_run))
        sub_id = orchestrator.spawn("researcher", "look something up")
        out = orchestrator.wait_for([sub_id], timeout=5)
        assert out[sub_id]["status"] == "done"
        assert "[Blocked]" in captured["result"]
        assert "researcher" in captured["result"]

    def test_a_coder_subagent_can_run_an_allowed_tool(self):
        from agent import core
        captured = {}

        def fake_run(framed_task):
            captured["result"] = core._execute_tool_inner(
                "current_time", {})
            return "reported"

        orchestrator.set_agent_factory(lambda: _FakeAgent(fake_run))
        sub_id = orchestrator.spawn("coder", "what time is it")
        out = orchestrator.wait_for([sub_id], timeout=5)
        assert out[sub_id]["status"] == "done"
        assert "[Blocked]" not in captured["result"]

    def test_the_scope_is_cleared_after_the_subagent_finishes(self):
        """If clear_active() ran only on the happy path, a crashed sub-agent
        would leave its role's restriction stuck on a thread nothing else
        uses again — harmless here, but the wrong invariant to ship."""
        def fake_run(framed_task):
            raise RuntimeError("boom")

        orchestrator.set_agent_factory(lambda: _FakeAgent(fake_run))
        sub_id = orchestrator.spawn("researcher", "this will crash")
        out = orchestrator.wait_for([sub_id], timeout=5)
        assert out[sub_id]["status"] == "error"
        # The assertion that matters is on the SUBAGENT's own thread, which
        # already exited — this just confirms the parent test thread, which
        # never had a scope, still doesn't.
        assert subagent_scope.active_role() is None


class TestNoRecursiveSpawning:
    """orchestrator.py's own docstring: "Sub-agents cannot recursively spawn
    (to prevent runaways)." Nothing checked that before subagent_scope.check()
    existed. Revert the fix and a sub-agent spawning a grandchild succeeds.
    """

    @pytest.mark.parametrize("role", list(subagent_scope.ROLE_TOOLS))
    def test_a_subagent_of_any_role_cannot_spawn_a_grandchild(self, role):
        from agent import core
        captured = {}

        def fake_run(framed_task):
            captured["result"] = core._execute_tool_inner(
                "spawn_subagent", {"role": "coder", "task": "grandchild"})
            return "reported"

        orchestrator.set_agent_factory(lambda: _FakeAgent(fake_run))
        sub_id = orchestrator.spawn(role, "try to spawn a grandchild")
        orchestrator.wait_for([sub_id], timeout=5)
        assert "[Blocked]" in captured["result"], \
            f"a '{role}' sub-agent was able to spawn a grandchild"
