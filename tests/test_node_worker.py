"""Delegated work executed through the node's own gates, not around them.

Step 6 of docs/PHASE_6_7_PLAN.md, and the step every earlier one was
protecting. node_tasks carries a request and has no `approved` column; this is
where that decision is cashed in.

`TestTheGatesRun` is the whole file's reason to exist. If a delegated command
could skip safety.check, the queue would be a permission bypass wearing a
feature's clothes — and it would look like it worked.
"""
import pytest

import config
from agent import longterm, node_tasks, node_worker, safety


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "n.db"))
    node_tasks.init_db()
    monkeypatch.setattr(config, "NODE_WORKER_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["bash", "current_time"],
                        raising=False)
    monkeypatch.setattr(node_worker, "_thread", None)
    # No console anywhere in a test, which is also the real headless case.
    monkeypatch.setattr(safety, "_confirm_fn", None)
    return "test-node"


def _task(kind="tool", **payload):
    return {"id": 1, "kind": kind, "payload": payload}


class TestTheGatesRun:
    """The success check: a delegated shell command still hits safety.check."""

    def test_safety_check_is_called_for_a_delegated_command(self, node, monkeypatch):
        seen = []
        real = safety.check

        def spy(name, inputs):
            seen.append((name, inputs))
            return real(name, inputs)
        monkeypatch.setattr(safety, "check", spy)
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)

        node_worker.run_one(_task(name="current_time", inputs={}))
        assert seen and seen[0][0] == "current_time", \
            "the delegated call did not reach safety.check — the queue would " \
            "be a permission bypass"

    def test_a_dangerous_delegated_command_is_blocked(self, node):
        """`rm -rf /` arriving from the Core is refused by safety on the machine
        that would have run it. With no confirm function installed the worker
        supplies its own, which says no."""
        ok, out, _perm = node_worker.run_one(
            _task(name="bash", inputs={"command": "rm -rf /"}))
        assert ok is False
        assert "BLOCKED by safety layer" in out

    def test_a_refusal_is_recorded_as_a_failure_not_a_success(self, node):
        """A refusal comes back as a STRING, not an exception. Treating it as a
        result would file "it was blocked" as "the tool returned some text"."""
        ok, out, _perm = node_worker.run_one(
            _task(name="bash", inputs={"command": "rm -rf /"}))
        assert ok is False and "BLOCKED" in out

    def test_it_goes_through_the_one_shared_executor(self, node, monkeypatch):
        """Not a second execution path. Two paths is how one of them gets a new
        gate and the other does not."""
        from agent import core
        called = []
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: called.append(n) or "ok")
        node_worker.run_one(_task(name="current_time", inputs={}))
        assert called == ["current_time"]

    def test_the_worker_answers_no_when_nobody_can_be_asked(self, node):
        """safety.check falls back to input() with no confirm function, which on
        a worker thread is a permanent hang — and on a headless node, worse: if
        some future stdin ever answered, an unattended queue would talk its way
        past the gate."""
        assert node_worker._confirm("something risky") is False

    def test_it_installs_that_answer_rather_than_leaving_input_in_place(
            self, node, monkeypatch):
        monkeypatch.setattr(safety, "_confirm_fn", None)

        def _boom(*a, **k):
            raise AssertionError("safety fell through to input() on a worker")
        monkeypatch.setattr("builtins.input", _boom)
        ok, out, _perm = node_worker.run_one(
            _task(name="bash", inputs={"command": "rm -rf /"}))
        assert ok is False


class TestTheRemoteAllowlist:
    """A second gate, asking a different question: safety judges the ACTION,
    NODE_TASK_TOOLS judges what may arrive over the network at all."""

    def test_a_tool_not_on_the_list_is_refused(self, node, monkeypatch):
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        ok, out, _perm = node_worker.run_one(_task(name="bash", inputs={"command": "ls"}))
        assert ok is False and "NODE_TASK_TOOLS" in out

    def test_the_refusal_says_what_is_allowed(self, node, monkeypatch):
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        assert "current_time" in node_worker.run_one(
            _task(name="bash", inputs={"command": "ls"}))[1]

    def test_an_empty_allowlist_runs_nothing(self, node, monkeypatch):
        """Deny-by-default. A node with the worker on and nothing named must not
        accept everything because nobody got round to narrowing it."""
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", [], raising=False)
        ok, out, _perm = node_worker.run_one(_task(name="current_time", inputs={}))
        assert ok is False and "Nothing is currently allowed" in out

    def test_the_allowlist_is_checked_before_anything_executes(
            self, node, monkeypatch):
        from agent import core
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", [], raising=False)

        def _boom(*a, **k):
            raise AssertionError("a disallowed tool reached the executor")
        monkeypatch.setattr(core, "_execute_tool", _boom)
        assert node_worker.run_one(_task(name="bash", inputs={}))[0] is False


class TestRefusalsThatAreNotAboutSafety:
    def test_an_unknown_task_kind_is_refused_not_improvised(self, node):
        ok, out, _perm = node_worker.run_one(_task(kind="teleport"))
        assert ok is False and "does not know how to run" in out

    def test_a_task_naming_no_tool_is_refused(self, node):
        ok, out, _perm = node_worker.run_one(_task(name="", inputs={}))
        assert ok is False and "named no tool" in out

    def test_non_object_inputs_are_refused(self, node):
        ok, out, _perm = node_worker.run_one(_task(name="bash", inputs="rm -rf /"))
        assert ok is False and "must be an object" in out

    def test_an_executor_exception_is_a_failure_not_a_crash(self, node, monkeypatch):
        from agent import core
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: (_ for _ in ()).throw(RuntimeError("boom")))
        ok, out, _perm = node_worker.run_one(_task(name="current_time", inputs={}))
        assert ok is False and "boom" in out


class TestDraining:
    def test_a_completed_task_is_marked_done(self, node, monkeypatch):
        from agent import capabilities as caps, core
        caps.init_db()
        monkeypatch.setattr(core, "_execute_tool", lambda n, i: "the time is now")
        t = node_tasks.submit("tool", payload={"name": "current_time", "inputs": {}})
        out = node_worker.drain_once("test-node")
        assert out["completed"] == 1
        row = node_tasks.get(t["id"])
        assert row["status"] == node_tasks.DONE and "time is now" in row["result"]

    def test_a_refused_task_carries_the_reason(self, node, monkeypatch):
        from agent import capabilities as caps
        caps.init_db()
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        t = node_tasks.submit("tool", payload={"name": "bash",
                                               "inputs": {"command": "ls"}})
        out = node_worker.drain_once("test-node")
        assert out["failed"] == 1
        assert "NODE_TASK_TOOLS" in node_tasks.get(t["id"])["error"]

    def test_it_stops_when_the_queue_is_empty(self, node):
        from agent import capabilities as caps
        caps.init_db()
        assert node_worker.drain_once("test-node") == {
            "node": "test-node", "completed": 0, "failed": 0}


class TestStartingUpSaysWhatItDecided:
    """Enabled-with-an-empty-allowlist and disabled both run zero tasks, and
    they need different fixes."""

    def test_disabled_says_so(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_WORKER_ENABLED", False, raising=False)
        monkeypatch.setattr(node_worker, "_thread", None)
        assert "NODE_WORKER_ENABLED=false" in node_worker.start_background()

    def test_enabled_with_nothing_allowed_says_that_instead(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_WORKER_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", [], raising=False)
        monkeypatch.setattr(node_worker, "_thread", None)
        line = node_worker.start_background()
        assert "NODE_TASK_TOOLS is empty" in line
        assert "every delegated task will be refused" in line

    def test_a_working_config_names_what_it_will_run(self, monkeypatch):
        monkeypatch.setattr(config, "NODE_WORKER_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        monkeypatch.setattr(node_worker, "_thread", None)
        ran = []
        monkeypatch.setattr(node_worker, "drain_once",
                            lambda *a, **k: ran.append(1) or {})
        try:
            line = node_worker.start_background()
            assert "current_time" in line and "Accepting" in line
        finally:
            node_worker.stop_background()
            if node_worker._thread:
                node_worker._thread.join(timeout=2)


class TestAPermanentRefusalIsNotRetried:
    """A tool that is not on the allowlist will not be on the allowlist ten
    seconds from now. Retrying it spends max_attempts in a tight loop and leaves
    the task recorded as "out of attempts" when the truth was "not allowed".

    Found by a test that expected one failure and got three: `fail()` puts a
    task back to `queued`, and the drain loop claimed it again immediately.
    """

    @pytest.fixture
    def caps(self, node):
        from agent import capabilities as c
        c.init_db()
        return node

    def test_a_disallowed_tool_is_tried_exactly_once(self, caps, monkeypatch):
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        t = node_tasks.submit("tool", payload={"name": "bash", "inputs": {}})
        out = node_worker.drain_once("test-node")
        assert out["failed"] == 1, "it was retried within a single drain"
        row = node_tasks.get(t["id"])
        assert row["status"] == node_tasks.DEAD
        assert row["attempts"] == 1, \
            "a refusal that cannot change must not spend the retry budget"

    def test_the_recorded_reason_is_the_real_one(self, caps, monkeypatch):
        """Not "out of attempts", which is what burning the budget produces."""
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        t = node_tasks.submit("tool", payload={"name": "bash", "inputs": {}})
        node_worker.drain_once("test-node")
        err = node_tasks.get(t["id"])["error"]
        assert "NODE_TASK_TOOLS" in err and "attempts" not in err

    def test_a_transient_failure_IS_retried(self, caps, monkeypatch):
        """The other half. A tool that threw once may work next time, so that
        one goes back to the queue rather than being retired."""
        from agent import core
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: (_ for _ in ()).throw(RuntimeError("flaky")))
        t = node_tasks.submit("tool", payload={"name": "current_time", "inputs": {}})
        node_worker.drain_once("test-node")
        row = node_tasks.get(t["id"])
        assert row["status"] == node_tasks.QUEUED, \
            "a transient failure must stay claimable"
        assert row["attempts"] == 1

    def test_a_safety_refusal_is_permanent(self, caps):
        """A delegated action safety refused should not keep coming back. The
        person should re-queue it deliberately."""
        t = node_tasks.submit("tool", payload={"name": "bash",
                                               "inputs": {"command": "rm -rf /"}})
        node_worker.drain_once("test-node")
        row = node_tasks.get(t["id"])
        assert row["status"] == node_tasks.DEAD and "BLOCKED" in row["error"]

    def test_the_drain_terminates_even_if_a_task_keeps_requeueing(
            self, caps, monkeypatch):
        """The loop is `while True`. Without the seen-set it would spin as long
        as anything kept coming back."""
        from agent import core
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"],
                            raising=False)
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: (_ for _ in ()).throw(RuntimeError("x")))
        node_tasks.submit("tool", payload={"name": "current_time", "inputs": {}})
        out = node_worker.drain_once("test-node")     # must return, not hang
        assert out["failed"] == 1
