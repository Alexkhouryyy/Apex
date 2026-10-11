"""Provider calls cannot bypass caps, lose usage, or silently retry paid streams."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

import config
from agent import budget, longterm, provider, telemetry


MODEL = "gpt-budget-test"
KWARGS = dict(model=MODEL, system="system", messages=[{"role": "user", "content": "question"}], max_tokens=100)


def response(usage=True):
    return NS(content=[NS(type="text", text="answer")], stop_reason="end_turn",
              usage=NS(input_tokens=10, output_tokens=2) if usage else None)


def client_for(create=None, stream=None):
    return NS(messages=NS(create=create or Mock(return_value=response()), stream=stream))


def ledger():
    with longterm._conn() as c:
        return c.execute("SELECT model, call_site, input_tokens, output_tokens, cost_usd, cost_status, stop_reason, session_id, turn_index FROM usage_log ORDER BY id").fetchall()


@pytest.fixture(autouse=True)
def accounting(test_db, monkeypatch):
    monkeypatch.setattr(config, "MODEL_PRICING", {MODEL: {k: 1.0 for k in telemetry._ZERO}})
    monkeypatch.setattr(telemetry, "_session_id", 123)
    monkeypatch.setattr(telemetry, "_turn_index", 7)
    monkeypatch.setattr(budget, "_initialized_for", None)
    budget.set_config({"enabled": True, "daily_usd": 1, "session_usd": 1})


def quote():
    ticket = budget.reserve(KWARGS)
    budget.release(ticket)
    return ticket.amount


def test_completion_records_actual_usage_once(monkeypatch):
    client = client_for()
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    assert provider.complete(MODEL, "system", "question", 100) == "answer"
    client.messages.create.assert_called_once()
    [row] = ledger()
    assert row[:4] == (MODEL, "provider.complete", 10, 2)
    assert row[4] == pytest.approx(12 / 1_000_000)
    assert row[5:] == ("usage", "end_turn", 123, 7)
    assert budget.reservations()["daily_reserved"] == 0


@pytest.mark.parametrize("limit", ["daily_usd", "session_usd"])
@pytest.mark.parametrize("streaming", [False, True])
def test_limit_rejects_before_provider_transport(monkeypatch, limit, streaming):
    budget.set_config({limit: quote() / 2})
    client = client_for(stream=Mock())
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    with pytest.raises(budget.BudgetExceeded):
        if streaming:
            provider.stream_complete(MODEL, "system", "question", 100, on_token=lambda t: None)
        else:
            provider.complete(MODEL, "system", "question", 100)
    client.messages.create.assert_not_called()
    client.messages.stream.assert_not_called()
    assert ledger() == []


def test_recorded_spend_is_checked_before_dispatch(monkeypatch):
    telemetry.record(call_site="previous", model=MODEL, usage=NS(input_tokens=1_000_000, output_tokens=0), latency_ms=0)
    client = client_for()
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    with pytest.raises(budget.BudgetExceeded):
        provider.complete(MODEL, "system", "question", 100)
    client.messages.create.assert_not_called()


def test_parallel_calls_cannot_share_remaining_allowance(monkeypatch):
    maximum = quote()
    budget.set_config({"daily_usd": maximum * 1.5, "session_usd": 0})
    start, executing, release = Barrier(2), Event(), Event()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        executing.set()
        assert release.wait(5)
        return response()

    monkeypatch.setattr(provider, "get_client", lambda model: client_for(create=create))
    def complete():
        start.wait(timeout=5)
        return provider.complete(MODEL, "system", "question", 100)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete) for _ in range(2)]
        try:
            assert executing.wait(5)
            done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            with pytest.raises(budget.BudgetExceeded):
                next(iter(done)).result()
            assert budget.reservations()["daily_reserved"] == pytest.approx(maximum)
        finally:
            release.set()
        assert sum(f.exception(timeout=5) is None for f in futures) == 1
    assert len(calls) == len(ledger()) == 1
    assert budget.reservations()["daily_reserved"] == 0


@pytest.mark.parametrize("mode", ["missing_usage", "exception"])
def test_unavailable_usage_does_not_refund_spending(monkeypatch, mode):
    maximum = quote()
    budget.set_config({"daily_usd": maximum * 1.5})
    create = Mock(return_value=response(False))
    if mode == "exception":
        create.side_effect = RuntimeError("provider timed out")
    monkeypatch.setattr(provider, "get_client", lambda model: client_for(create=create))
    if mode == "exception":
        with pytest.raises(RuntimeError, match="timed out"):
            provider.complete(MODEL, "system", "question", 100)
    else:
        assert provider.complete(MODEL, "system", "question", 100) == "answer"
    [row] = ledger()
    assert row[4] == pytest.approx(maximum) and row[5] == "estimate"
    with pytest.raises(budget.BudgetExceeded):
        provider.complete(MODEL, "system", "question", 100)
    create.assert_called_once()
    assert telemetry.summary()["estimated_cost_calls"] == 1


def test_ledger_failure_keeps_reservation(monkeypatch):
    original = longterm._ensure_usage_schema
    calls = [0]
    def fail_record(conn):
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("ledger unavailable")
        return original(conn)
    maximum = quote()
    budget.set_config({"daily_usd": maximum * 1.5})
    monkeypatch.setattr(longterm, "_ensure_usage_schema", fail_record)
    client = client_for()
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    assert provider.complete(MODEL, "system", "question", 100) == "answer"
    assert ledger() == []
    assert budget.reservations()["daily_reserved"] == pytest.approx(maximum)
    with pytest.raises(budget.BudgetExceeded):
        provider.complete(MODEL, "system", "question", 100)
    client.messages.create.assert_called_once()


def test_reservations_survive_reinitialization():
    maximum = quote()
    budget.set_config({"daily_usd": maximum * 1.5})
    ticket = budget.reserve(KWARGS)
    budget.init_db()
    with pytest.raises(budget.BudgetExceeded):
        budget.reserve(KWARGS)
    budget.release(ticket)


@pytest.mark.parametrize("invalid", [None, {"input": -1, "output": 1, "cache_read": 0, "cache_create": 0},
    {"input": float("nan"), "output": 1, "cache_read": 0, "cache_create": 0}])
def test_unknown_or_invalid_remote_price_is_not_free(monkeypatch, invalid):
    monkeypatch.setattr(config, "MODEL_PRICING", {MODEL: invalid} if invalid is not None else {})
    client = client_for()
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    with pytest.raises(budget.BudgetUnavailable, match="MODEL_PRICING_JSON"):
        provider.complete(MODEL, "system", "question", 100)
    client.messages.create.assert_not_called()


def test_explicitly_disabled_caps_keep_unknown_cost_visible(monkeypatch):
    budget.set_config({"enabled": False})
    monkeypatch.setattr(config, "MODEL_PRICING", {})
    monkeypatch.setattr(provider, "get_client", lambda model: client_for())
    assert provider.complete(MODEL, "system", "question", 100) == "answer"
    assert ledger()[0][4:6] == (None, "unknown")
    summary = telemetry.summary()
    assert summary["total_cost_usd"] is None and summary["unknown_cost_calls"] == 1
    assert summary["by_model"][0]["cost_usd"] is None
    assert summary["by_day"][0]["cost_usd"] is None
    with longterm._conn() as c:
        c.execute("INSERT INTO sessions (id, started_at) VALUES (?, ?)", (123, 1))
    assert telemetry.list_recent_sessions()[0]["cost_usd"] is None
    assert telemetry.replay_session(123)["usage"][0]["cost_status"] == "unknown"
    budget.set_config({"enabled": True})
    assert "cannot be verified" in budget.check()


def test_calls_without_session_still_share_an_allowance(monkeypatch):
    monkeypatch.setattr(telemetry, "_session_id", None)
    budget.set_config({"daily_usd": 0, "session_usd": quote() * 1.5})
    ticket = budget.reserve(KWARGS)
    with pytest.raises(budget.BudgetExceeded):
        budget.reserve(KWARGS)
    assert budget.reservations()["session_reserved"] == ticket.amount
    telemetry.record(call_site="no-session", model=MODEL, usage=None, latency_ms=0, reservation=ticket)
    assert budget.session_spend() == ticket.amount
    with pytest.raises(budget.BudgetExceeded):
        budget.reserve(KWARGS)


def test_local_model_remains_free_when_paid_cap_is_reached(monkeypatch):
    telemetry.record(call_site="previous", model=MODEL, usage=NS(input_tokens=1_000_000, output_tokens=0), latency_ms=0)
    monkeypatch.setattr(provider, "get_client", lambda model: client_for())
    assert provider.complete("ollama/local", "system", "question", 100) == "answer"
    assert ledger()[-1][4] == 0


class Stream:
    def __init__(self, error=False, usage=True):
        self.error, self.usage = error, usage
        self.final_calls = 0
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def __iter__(self):
        yield provider._Event("answer")
        if self.error:
            raise RuntimeError("stream connection lost")
    def get_final_message(self):
        self.final_calls += 1
        return response(self.usage)


def test_stream_records_usage_once(monkeypatch):
    source = Stream()
    client = client_for(stream=Mock(return_value=source))
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    seen = []
    assert provider.stream_complete(MODEL, "system", "question", 100, seen.append) == "answer"
    assert seen == ["answer"] and source.final_calls == 1
    assert ledger()[0][1] == "provider.stream_complete" and ledger()[0][5] == "usage"
    client.messages.create.assert_not_called()


def test_failed_stream_is_recorded_and_never_retried(monkeypatch):
    source = Stream(error=True)
    client = client_for(stream=Mock(return_value=source))
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    with pytest.raises(RuntimeError, match="connection lost"):
        provider.stream_complete(MODEL, "system", "question", 100, lambda t: None)
    client.messages.create.assert_not_called()
    assert source.final_calls == 0
    assert ledger()[0][5:7] == ("estimate", "stream_interrupted")


def test_cancelled_stream_does_not_drain_or_refund():
    source = Stream()
    with telemetry.stream(client_for(stream=lambda **k: source), call_site="cancel-test", **KWARGS) as stream:
        for event in stream:
            break
    assert source.final_calls == 0
    assert ledger()[0][5:7] == ("estimate", "stream_interrupted")


def test_unsupported_stream_falls_back_once(monkeypatch):
    client = client_for(stream=Mock(side_effect=NotImplementedError("unsupported")))
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    assert provider.stream_complete(MODEL, "system", "question", 100, lambda t: None) == "answer"
    client.messages.create.assert_called_once()
    assert len(ledger()) == 1 and ledger()[0][5] == "usage"
    assert budget.reservations()["daily_reserved"] == 0


def test_adapter_does_not_invent_usage_or_repeat_interrupted_stream():
    assert provider._wrap_response(NS(choices=[], usage=None)).usage is None
    transport = Mock(return_value=iter([NS(choices=[NS(delta=NS(content="partial", tool_calls=None), finish_reason=None)], usage=None)]))
    client = NS(chat=NS(completions=NS(create=transport)))
    source = provider._OpenAIStream(client, {"model": MODEL})
    assert next(iter(source)).delta.text == "partial"
    with pytest.raises(RuntimeError, match="incomplete"):
        source.get_final_message()
    transport.assert_called_once()


def test_cached_input_is_not_charged_twice():
    usage = provider._wrap_usage(NS(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=NS(cached_tokens=60)))
    assert usage.input_tokens == 40 and usage.cache_read_input_tokens == 60


def test_call_keeps_original_session_metadata(monkeypatch):
    def create(**kwargs):
        telemetry.set_session(999)
        return response()
    monkeypatch.setattr(provider, "get_client", lambda model: client_for(create=create))
    provider.complete(MODEL, "system", "question", 100)
    assert ledger()[0][7:] == (123, 7)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_limits_cannot_disable_caps(value):
    with pytest.raises(ValueError):
        budget.set_config({"daily_usd": value, "enabled": False})
    assert budget.get_config()["enabled"] is True


def test_legacy_ledger_upgrades_without_losing_rows():
    telemetry.record(call_site="legacy", model=MODEL, usage=NS(input_tokens=10, output_tokens=2), latency_ms=0)
    with longterm._conn() as c:
        c.execute("ALTER TABLE usage_log DROP COLUMN cost_status")
    budget.init_db()
    assert ledger()[0][1] == "legacy" and ledger()[0][5] == "usage"


@pytest.mark.parametrize("usage", [NS(input_tokens=-100, output_tokens=2), NS(input_tokens=None, output_tokens=2), NS(input_tokens=False, output_tokens=2), NS()])
def test_invalid_usage_cannot_reduce_spending(monkeypatch, usage):
    final = response()
    final.usage = usage
    monkeypatch.setattr(provider, "get_client", lambda model: client_for(create=lambda **kw: final))
    maximum = quote()
    provider.complete(MODEL, "system", "question", 100)
    assert ledger()[0][4] == pytest.approx(maximum) and ledger()[0][5] == "estimate"


@pytest.mark.parametrize("usage", [NS(), NS(prompt_tokens=None, completion_tokens=2),
                                   NS(prompt_tokens=False, completion_tokens=2),
                                   NS(prompt_tokens=10, completion_tokens=2,
                                      prompt_tokens_details=NS(cached_tokens=11))])
def test_adapter_does_not_invent_missing_or_invalid_usage(usage):
    assert provider._wrap_usage(usage) is None


@pytest.mark.parametrize("caller", ["master", "paired", "tokenless"])
def test_only_owner_can_change_limits(monkeypatch, caller):
    from fastapi.testclient import TestClient
    from agent import access_tokens
    from dashboard import server
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "budget-master")
    headers = {"Authorization": "Bearer budget-master"}
    if caller == "paired":
        access_tokens.init_db()
        headers["Authorization"] = f"Bearer {access_tokens.issue(label='device')}"
    elif caller == "tokenless":
        monkeypatch.setattr(config, "DASHBOARD_TOKEN", "")
        headers = {}
    result = TestClient(server.app).post("/api/budget", json={"enabled": False}, headers=headers)
    assert result.status_code == (200 if caller == "master" else 403)
    assert budget.get_config()["enabled"] is (caller != "master")


def test_budget_api_exposes_reservations_and_validates_settings(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard import server
    monkeypatch.setattr(config, "DASHBOARD_TOKEN", "budget-master")
    client = TestClient(server.app, headers={"Authorization": "Bearer budget-master"})
    ticket = budget.reserve(KWARGS)
    result = client.get("/api/budget")
    assert result.status_code == 200
    assert result.json()["daily_reserved"] == ticket.amount
    assert client.post("/api/budget", json={"daily_usd": -1}).status_code == 400
    budget.release(ticket)


@pytest.mark.parametrize("limited", [False, True])
def test_world_assistant_cannot_bypass_accounting(monkeypatch, limited):
    from dashboard import world_assistant
    client = client_for()
    monkeypatch.setattr(provider, "get_client", lambda model: client)
    if limited:
        budget.set_config({"daily_usd": 0.000001})
        with pytest.raises(budget.BudgetExceeded):
            world_assistant._complete(MODEL, KWARGS["messages"], [])
        client.messages.create.assert_not_called()
    else:
        assert world_assistant._complete(MODEL, KWARGS["messages"], []) == [{"type": "text", "text": "answer"}]
        assert ledger()[0][1] == "world_assistant/turn" and ledger()[0][5] == "usage"


@pytest.mark.parametrize("limited", [False, True])
def test_fallback_cannot_bypass_accounting(monkeypatch, limited):
    from agent import resilience
    from openai import OpenAI
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "fake-key")
    monkeypatch.setattr(config, "FALLBACK_MODEL", MODEL)
    transport = Mock(return_value=NS(choices=[NS(message=NS(content="answer"), finish_reason="stop")],
                                   usage=NS(prompt_tokens=10, completion_tokens=2)))
    monkeypatch.setattr(OpenAI, "__init__", lambda self, **kw:
                        setattr(self, "chat", NS(completions=NS(create=transport))))
    if limited:
        budget.set_config({"daily_usd": 0.000001})
        with pytest.raises(RuntimeError, match="spend cap"):
            resilience.fallback_create(KWARGS["messages"], max_tokens=100)
        transport.assert_not_called()
    else:
        assert resilience.fallback_create(KWARGS["messages"], max_tokens=100) == "answer"
        transport.assert_called_once()
        assert ledger()[0][1] == "resilience/fallback" and ledger()[0][5] == "usage"
