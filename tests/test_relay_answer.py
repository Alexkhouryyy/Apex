"""The cloud answering, and the laptop refusing to take orders from it.

Step 8 of docs/PHASE_6_7_PLAN.md. The success check is two clauses and both have
a class: "the laptop files it exactly once" (`TestFilingIsExactlyOnce`) and "an
account-touching request is queued rather than performed"
(`TestAReplyIsDataNeverAnInstruction`).

The second is the one that matters. A reply is written on a machine you may not
own, by a model, from a context that machine could read. If the relay were
compromised an attacker could put "run rm -rf /" in one — so the worst it can
achieve must be a wrong answer and a task sitting visibly in the queue.
"""
import importlib.util
import json
import socket
import threading
from pathlib import Path

import pytest

import config
from agent import longterm, node_tasks, relay

ROOT = Path(__file__).resolve().parent.parent


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "relay_server", ROOT / "relay" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.TOKEN = "tok"
    port = _free_port()
    srv = mod.serve(host="127.0.0.1", port=port,
                    db_path=str(tmp_path / "relay.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setattr(longterm, "DB_PATH", str(tmp_path / "brain.db"))
    from agent import schema
    schema.init_all(log=lambda *a: None)
    monkeypatch.setattr(config, "RELAY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RELAY_URL", f"http://127.0.0.1:{port}", raising=False)
    monkeypatch.setattr(config, "RELAY_TOKEN", "tok", raising=False)
    monkeypatch.setattr(config, "RELAY_KEY", relay.new_key(), raising=False)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown(); srv.server_close()


def _write_reply(**payload):
    relay._http("POST", "/reply", json.dumps(payload).encode())


def _memories():
    with longterm._conn() as c:
        return [r[0] for r in c.execute("SELECT content FROM memories")]


class TestAReplyIsDataNeverAnInstruction:
    def test_a_requested_action_is_queued_not_performed(self, live, monkeypatch):
        """The success check's second clause. The cloud can want something to
        happen; it cannot be the thing that approves it."""
        from agent import core
        ran = []
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: ran.append(n) or "done")
        _write_reply(question="email Bob", answer="I cannot send mail from here.",
                     requests=[{"tool": "send_email", "why": "the user asked"}])
        out = relay.drain_replies()
        assert out["queued"] == 1
        assert ran == [], "the cloud's request was executed instead of queued"
        assert [t["kind"] for t in node_tasks.pending()] == ["tool"]

    def test_a_hostile_reply_cannot_run_a_command(self, live, monkeypatch):
        """If the relay is compromised, the worst it achieves is a task you can
        see sitting in the queue."""
        from agent import core
        ran = []
        monkeypatch.setattr(core, "_execute_tool",
                            lambda n, i: ran.append(n) or "done")
        _write_reply(question="hi", answer="Sure.",
                     requests=[{"tool": "bash", "inputs": {"command": "rm -rf /"}}])
        relay.drain_replies()
        assert ran == []
        assert node_tasks.pending()[0]["status"] == node_tasks.QUEUED

    def test_the_queued_task_still_faces_the_local_gates(self, live, monkeypatch):
        """Queued is not approved. It meets NODE_TASK_TOOLS and safety.check on
        the way out, exactly like anything else."""
        from agent import capabilities, node_worker, safety
        capabilities.init_db()
        monkeypatch.setattr(config, "NODE_WORKER_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "NODE_TASK_TOOLS", ["current_time"], raising=False)
        monkeypatch.setattr(safety, "_confirm_fn", None)
        _write_reply(question="x", answer="ok",
                     requests=[{"tool": "bash", "inputs": {"command": "rm -rf /"}}])
        relay.drain_replies()
        out = node_worker.drain_once("this-node")
        assert out["completed"] == 0 and out["failed"] == 1

    def test_the_answer_is_filed_as_content_not_executed(self, live):
        _write_reply(question="what did I say about coffee?",
                     answer="You prefer dark roast.")
        relay.drain_replies()
        got = "\n".join(_memories())
        assert "dark roast" in got and "Answered from the relay" in got

    def test_requests_are_capped(self, live):
        """A compromised relay must not be able to fill the queue."""
        _write_reply(question="x", answer="y",
                     requests=[{"tool": f"t{i}"} for i in range(50)])
        assert relay.drain_replies()["queued"] == relay.REPLY_MAX_REQUESTS

    def test_malformed_requests_are_ignored_not_fatal(self, live):
        _write_reply(question="x", answer="y",
                     requests=["not a dict", {"no_tool": 1}, {"tool": ""}])
        out = relay.drain_replies()
        assert out["ok"] is True and out["queued"] == 0 and out["filed"] == 1


class TestFilingIsExactlyOnce:
    def test_draining_twice_files_once(self, live):
        _write_reply(question="q", answer="the answer")
        relay.drain_replies()
        relay.drain_replies()
        assert sum("the answer" in m for m in _memories()) == 1

    def test_a_lost_acknowledgement_does_not_refile(self, live):
        real = relay._http

        def no_ack(method, path, body=None, timeout=30.0):
            if path.endswith("/done"):
                raise relay.RelayError("ack lost")
            return real(method, path, body, timeout)

        _write_reply(question="q", answer="once only")
        relay._http = no_ack
        try:
            relay.drain_replies()
            assert relay.pull_replies(), "the relay should still be offering it"
        finally:
            relay._http = real
        relay.drain_replies()
        assert sum("once only" in m for m in _memories()) == 1

    def test_the_relay_stops_offering_it_after_filing(self, live):
        _write_reply(question="q", answer="a")
        relay.drain_replies()
        assert relay.pull_replies() == []

    def test_requests_are_queued_once_too(self, live):
        _write_reply(question="q", answer="a", requests=[{"tool": "current_time"}])
        relay.drain_replies()
        relay.drain_replies()
        assert len(node_tasks.pending()) == 1


class TestRefusals:
    def test_the_server_refuses_an_empty_answer(self, live):
        """An empty reply filed as a memory is noise the laptop then has to
        carry for ever."""
        with pytest.raises(relay.RelayError) as e:
            _write_reply(question="q", answer="   ")
        assert "400" in str(e.value)

    def test_the_server_refuses_non_json(self, live):
        with pytest.raises(relay.RelayError):
            relay._http("POST", "/reply", b"not json at all")

    def test_draining_while_disabled_does_nothing(self, live, monkeypatch):
        _write_reply(question="q", answer="a")
        monkeypatch.setattr(config, "RELAY_ENABLED", False, raising=False)
        assert relay.drain_replies()["ok"] is False
        assert _memories() == []


class TestTheAnswererIsSeparate:
    """Answering is opt-in at the level of which PROCESSES you start, not a flag
    inside one that a config you did not write could flip."""

    def test_the_mailbox_holds_no_model_key(self):
        src = (ROOT / "relay" / "server.py").read_text()
        for forbidden in ("ANTHROPIC_API_KEY", "api.anthropic.com", "x-api-key"):
            assert forbidden not in src, (
                f"relay/server.py mentions {forbidden}. The mailbox must not "
                f"need a model key — that is why answering is a separate file.")

    def test_the_answerer_imports_nothing_from_apex(self):
        """It runs on the rented box, not on the laptop."""
        import ast
        tree = ast.parse((ROOT / "relay" / "answer.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") or \
                      ",".join(a.name for a in node.names)
                assert not mod.startswith(("agent", "config")), \
                    f"relay/answer.py imports {mod}"

    def test_it_refuses_to_run_without_a_key(self, monkeypatch):
        spec = importlib.util.spec_from_file_location(
            "relay_answer", ROOT / "relay" / "answer.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.TOKEN, mod.API_KEY = "tok", ""
        assert mod.main(["answer.py", "hello"]) == 2

    def test_a_chatty_model_answer_is_still_an_answer(self):
        """A model asked for JSON does not always send JSON. Discarding a
        slightly malformed reply would turn "the model was chatty" into "the
        cloud is down"."""
        spec = importlib.util.spec_from_file_location(
            "relay_answer", ROOT / "relay" / "answer.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = json.dumps({"content": [{"type": "text",
                                       "text": "Sure! Here you go."}]}).encode()
        out = mod.ask_model("q", "ctx", call=lambda body: raw)
        assert out["answer"] == "Sure! Here you go." and out["requests"] == []

    def test_a_well_formed_answer_is_parsed(self):
        spec = importlib.util.spec_from_file_location(
            "relay_answer", ROOT / "relay" / "answer.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        payload = json.dumps({"answer": "dark roast",
                              "requests": [{"tool": "send_email"}]})
        raw = json.dumps({"content": [{"type": "text", "text": payload}]}).encode()
        out = mod.ask_model("q", "ctx", call=lambda body: raw)
        assert out["answer"] == "dark roast"
        assert out["requests"] == [{"tool": "send_email"}]

    def test_no_context_yet_says_so_rather_than_inventing(self, live):
        spec = importlib.util.spec_from_file_location(
            "relay_answer", ROOT / "relay" / "answer.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.SERVER, mod.TOKEN = config.RELAY_URL, "tok"

        def _must_not_run(body):
            raise AssertionError("asked a model with no context to answer from")
        out = mod.answer("what do I like?", call=_must_not_run)
        assert "not sent me anything" in out["answer"]
