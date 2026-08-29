"""Enforcing what a sub-agent's role prompt only asked for politely.

Before this file, `agent/orchestrator.py`'s ROLE_PROMPTS told a researcher
"Do NOT take actions on the user's computer" and told nothing to stop it if
the model decided otherwise — the sentence was the entire enforcement. The
docstring also claimed sub-agents "cannot recursively spawn (to prevent
runaways)"; nothing checked that either. Both are exactly the shape this
project keeps finding: a restriction that is real in the comment and nowhere
in the code.
"""
import threading

import pytest

from agent import subagent_scope


class TestIsAllowedIsAnAllowlist:
    """A denylist silently permits every new tool until someone remembers to
    add it. An allowlist fails the other way — that is the point."""

    @pytest.mark.parametrize("role,tool", [
        ("researcher", "web_search"),
        ("researcher", "kb_search"),
        ("coder", "bash"),
        ("coder", "write_file"),
        ("browser", "browser_goto"),
        ("analyst", "python_exec"),
        ("writer", "web_search"),
    ])
    def test_a_role_gets_what_its_prompt_promises(self, role, tool):
        assert subagent_scope.is_allowed(role, tool)

    @pytest.mark.parametrize("role,tool", [
        ("researcher", "bash"),
        ("researcher", "write_file"),
        ("writer", "bash"),
        ("writer", "write_file"),
        ("analyst", "bash"),
        ("browser", "python_exec"),
    ])
    def test_a_role_does_not_get_what_its_prompt_never_claimed(self, role, tool):
        """THE regression this file exists for. Before the guard, every one of
        these succeeded — a researcher's `bash` call ran on the real machine."""
        assert not subagent_scope.is_allowed(role, tool)

    def test_the_planner_gets_no_tools_at_all(self):
        """Its prompt is 'return JSON', not 'take action'. A planner calling
        any tool has stopped planning."""
        for tool in ("bash", "web_search", "read_file", "python_exec"):
            assert not subagent_scope.is_allowed("planner", tool)

    @pytest.mark.parametrize("role", list(subagent_scope.ROLE_TOOLS))
    def test_no_role_can_spawn_a_grandchild(self, role):
        """THE recursion claim. orchestrator.py's docstring says sub-agents
        cannot recursively spawn; this is the only place that was ever true."""
        assert not subagent_scope.is_allowed(role, "spawn_subagent")
        assert not subagent_scope.is_allowed(role, "wait_for_subagents")

    def test_an_unknown_role_gets_nothing(self):
        """Fail closed on a typo'd or future role name, not fail open."""
        assert not subagent_scope.is_allowed("intern", "current_time")


class TestScopeIsThreadLocal:
    def test_no_active_scope_means_no_restriction(self):
        """The main agent, the dashboard, and the resident loop never call
        set_active — check() must be a true no-op for them."""
        subagent_scope.clear_active()
        assert subagent_scope.active_role() is None
        assert subagent_scope.check("bash") is None
        assert subagent_scope.check("spawn_subagent") is None

    def test_check_refuses_with_the_role_and_the_tool_named(self):
        subagent_scope.set_active("researcher")
        try:
            msg = subagent_scope.check("bash")
            assert msg is not None
            assert "researcher" in msg and "bash" in msg
            assert "web_search" in msg, "must say what IS allowed, not just what isn't"
        finally:
            subagent_scope.clear_active()

    def test_check_allows_a_permitted_tool(self):
        subagent_scope.set_active("coder")
        try:
            assert subagent_scope.check("write_file") is None
        finally:
            subagent_scope.clear_active()

    def test_one_threads_scope_does_not_touch_another(self):
        """The actual isolation claim. Two 'sub-agents' on two threads must
        not see each other's role — that is what makes concurrent sub-agents
        with different roles safe at all."""
        results = {}

        def run_as(role, tool):
            subagent_scope.set_active(role)
            results[role] = subagent_scope.check(tool)

        t1 = threading.Thread(target=run_as, args=("researcher", "bash"))
        t2 = threading.Thread(target=run_as, args=("coder", "bash"))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert results["researcher"] is not None, "researcher must be refused"
        assert results["coder"] is None, "coder must be allowed"

    def test_the_main_thread_is_unaffected_by_a_spawned_threads_scope(self):
        """A sub-agent thread setting its own role must never leak onto the
        thread that spawned it."""
        subagent_scope.clear_active()

        def run_as_researcher():
            subagent_scope.set_active("researcher")

        t = threading.Thread(target=run_as_researcher)
        t.start(); t.join()

        assert subagent_scope.active_role() is None
