"""Real Chronicle recording/replay, with no live providers or tool effects."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

chronicle = pytest.importorskip("chronicle")
from chronicle import session as sessions
import config
from agent import incident_replay as incidents, router, safety


def test_disabled_does_not_import_optional_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INCIDENT_RECORDING_ENABLED", False)
    monkeypatch.setattr(incidents, "_library", lambda: pytest.fail("optional import"))
    with incidents.record_turn() as session:
        assert session is None
        assert router.route_model("hello", "claude-sonnet-5") == ("claude-sonnet-5", None)
    assert not list(tmp_path.iterdir())


def test_router_roundtrip_and_live_cutpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "ROUTING_SIMPLE_MODEL", "claude-haiku-4-5")
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture) as session:
        expected = router.route_model("hello", "claude-sonnet-5")
    assert expected == ("claude-haiku-4-5", "simple")
    assert session.envelopes[0].name == "apex.route_model"
    monkeypatch.setattr(config, "SMART_ROUTING_ENABLED", False)
    for _ in range(20):
        with incidents.replay(fixture):
            assert router.route_model("hello", "claude-sonnet-5") == expected
    with incidents.replay(fixture, live_router=True) as replayed:
        assert router.route_model("hello", "claude-sonnet-5") == ("claude-sonnet-5", None)
        assert replayed.captured_result("apex.route_model", 1) == ("claude-sonnet-5", None)


def test_tool_replay_never_repeats_real_effect_or_observer(monkeypatch, tmp_path):
    from agent import core
    calls = []
    monkeypatch.setattr(core, "_execute_tool_inner", lambda name, args: calls.append(name) or "done")
    monkeypatch.setattr(core, "_observe", lambda event: None)
    monkeypatch.setattr(core.plugins, "emit", lambda *a, **kw: None)
    from agent import trajectory, observed, recovery
    monkeypatch.setattr(trajectory, "record", lambda *a, **kw: None)
    monkeypatch.setattr(observed, "note_tool_result", lambda *a, **kw: None)
    monkeypatch.setattr(recovery, "enrich", lambda name, args, result: result)
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        assert core._execute_tool("write_file", {"path": "demo.txt", "content": "hello"}) == "done"
    monkeypatch.setattr(core, "_observe", lambda event: pytest.fail("observer executed"))
    with incidents.replay(fixture):
        assert core._execute_tool("write_file", {"path": "demo.txt", "content": "hello"}) == "done"
    assert calls == ["write_file"]


def test_safety_replay_does_not_ask_again(tmp_path):
    safety.set_confirm_fn(lambda _: False)
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        expected = safety.check("bash", {"command": "rm -rf /tmp/example"})
    assert expected[0] is False
    safety.set_confirm_fn(lambda _: pytest.fail("confirmation repeated"))
    with incidents.replay(fixture):
        assert safety.check("bash", {"command": "rm -rf /tmp/example"}) == expected


def test_real_dispatch_records_nested_refusal_and_replays_it(monkeypatch, tmp_path):
    from agent import core, trajectory, observed, recovery
    monkeypatch.setattr(core.plugins, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(core, "_observe", lambda event: None)
    monkeypatch.setattr(trajectory, "record", lambda *a, **kw: None)
    monkeypatch.setattr(observed, "note_tool_result", lambda *a, **kw: None)
    monkeypatch.setattr(recovery, "enrich", lambda name, args, result: result)
    monkeypatch.setattr(core.bash, "run", lambda *a, **kw: pytest.fail("real command executed"))
    safety.set_confirm_fn(lambda _: False)
    fixture = tmp_path / "fixture"
    inputs = {"command": "rm -rf /tmp/example"}
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture) as session:
        expected = core._execute_tool("bash", inputs)
    assert "BLOCKED by safety layer" in expected
    tool = next(e for e in session.envelopes if e.name == "apex.execute_tool")
    permission = next(e for e in session.envelopes if e.name == "apex.safety_check")
    assert permission.parent_envelope_id == tool.envelope_id
    safety.set_confirm_fn(lambda _: pytest.fail("confirmation repeated"))
    with incidents.replay(fixture):
        assert core._execute_tool("bash", inputs) == expected


def test_full_agent_turn_refused_before_orchestration(tmp_path):
    from agent.core import AgentCore
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        router.route_model("hello", "claude-sonnet-5")
    # Uninitialized core: reaching any orchestration would fail for other reasons.
    core = AgentCore.__new__(AgentCore)
    with incidents.replay(fixture):
        with pytest.raises(RuntimeError, match="Full agent turns"):
            core.run("hello")


def test_secrets_removed_from_memory_disk_and_export(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_API_KEY", "arbitrary-configured-value")

    @incidents.boundary("test.secret", kind="tool")
    def call(payload):
        return json.dumps({"password": "short", "nested": {"token": "tiny"},
                           "text": "arbitrary-configured-value sk-abcdefghijklmnopqrstuv"})

    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture) as session:
        raw = call({"password": "short", "note": "arbitrary-configured-value"})
        assert "short" in raw  # caller's real output is unchanged
        retained = json.dumps([e.model_dump(mode="json") for e in session.envelopes])
    stored = retained + "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    for secret in ("short", "tiny", "arbitrary-configured-value", "sk-abcdefghijklmnopqrstuv"):
        assert secret not in stored
    assert "[REDACTED]" in stored


def test_store_failure_cannot_duplicate_an_action(monkeypatch, tmp_path):
    calls = []

    @incidents.boundary("test.effect", kind="tool")
    def effect():
        calls.append(1)
        return "done"

    def broken(*a):
        raise OSError("disk full")

    with incidents.record_turn(store=tmp_path / "run.jsonl") as session:
        monkeypatch.setattr(session.store.store, "append", broken)
        assert effect() == "done"
    assert calls == [1]


def test_capture_failure_after_effect_cannot_retry(monkeypatch, tmp_path):
    calls = []

    @incidents.boundary("test.effect", kind="tool")
    def effect():
        calls.append(1)
        return "done"

    def broken(*a, **kw):
        raise RuntimeError("capture failed")

    with incidents.record_turn(store=tmp_path / "run.jsonl") as session:
        monkeypatch.setattr(session, "record_envelope", broken)
        assert effect() == "done"
    assert calls == [1]


def test_original_exception_preserved_even_when_recording_fails(monkeypatch, tmp_path):
    error = ValueError("original")
    calls = []

    @incidents.boundary("test.failure")
    def fail():
        calls.append(1)
        raise error

    with incidents.record_turn(store=tmp_path / "run.jsonl") as session:
        monkeypatch.setattr(session, "record_envelope", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
        with pytest.raises(ValueError) as raised:
            fail()
        assert raised.value is error
    assert calls == [1]


def test_failed_crossing_has_redacted_error_fixture(tmp_path):
    @incidents.boundary("test.failure")
    def fail():
        raise ValueError("password=secretvalue")

    fixture = tmp_path / "fixture"
    with pytest.raises(ValueError):
        with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture) as session:
            fail()
    assert session.envelopes[0].status.code == "ERROR"
    assert "secretvalue" not in (tmp_path / "run.jsonl").read_text()
    assert fixture.exists()
    with incidents.replay(fixture):
        with pytest.raises(incidents.RecordedBoundaryError, match="REDACTED"):
            fail()


def test_replay_rejects_effectful_live_override(tmp_path):
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        safety.check("bash", {"command": "rm -rf /tmp/example"})
    with incidents.replay(fixture) as session:
        session.replay_plan.live("apex.safety_check")
        with pytest.raises(RuntimeError, match="Only the pure"):
            safety.check("bash", {"command": "rm -rf /tmp/example"})


def test_context_restored_after_success_and_failure(tmp_path):
    previous = sessions.peek_session()
    with incidents.record_turn(store=tmp_path / "run.jsonl") as outer:
        with incidents.record_turn(store=tmp_path / "ignored.jsonl") as nested:
            assert nested is outer
        assert incidents._active.get() is outer
    assert sessions.peek_session() is previous
    assert incidents._active.get() is None
    with pytest.raises(ValueError):
        with incidents.record_turn(store=tmp_path / "failed.jsonl"):
            raise ValueError("body")
    assert sessions.peek_session() is previous
    assert not (tmp_path / "ignored.jsonl").exists()


def test_concurrent_turns_are_isolated(tmp_path):
    def turn(index):
        with incidents.record_turn(str(index), store=tmp_path / f"{index}.jsonl") as session:
            router.route_model("hello", "claude-sonnet-5")
            return session.trace_id, session.envelopes[0].attributes["session_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(turn, range(8)))
    assert len({trace for trace, _ in results}) == 8
    assert [channel for _, channel in results] == [str(i) for i in range(8)]


def test_missing_replay_boundary_never_falls_through(tmp_path):
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        router.route_model("hello", "claude-sonnet-5")
    with incidents.replay(fixture):
        with pytest.raises((KeyError, IndexError, ValueError)):
            safety.check("bash", {"command": "rm -rf /tmp/example"})


def test_replay_context_restored_on_error(tmp_path):
    fixture = tmp_path / "fixture"
    with incidents.record_turn(store=tmp_path / "run.jsonl", export=fixture):
        router.route_model("hello", "claude-sonnet-5")
    previous = sessions.peek_session()
    with pytest.raises(ValueError):
        with incidents.replay(fixture):
            raise ValueError("body")
    assert sessions.peek_session() is previous
    assert incidents._active.get() is None


def test_library_unavailable_keeps_turn_running(monkeypatch, tmp_path):
    def missing():
        raise ImportError("not installed")
    monkeypatch.setattr(incidents, "_library", missing)
    with incidents.record_turn(store=tmp_path / "run.jsonl") as session:
        assert session is None
        assert router.route_model("hello", "claude-sonnet-5")[0] == "claude-sonnet-5"


def test_chronicle_disabled_does_not_swallow_body_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("CHRONICLE_ENABLED", "false")
    with pytest.raises(ValueError, match="body"):
        with incidents.record_turn(store=tmp_path / "run.jsonl") as session:
            assert session is None
            raise ValueError("body")
