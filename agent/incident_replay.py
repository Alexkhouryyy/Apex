"""Optional Chronicle integration. Recording never retries a real action.

Replay is a boundary test bench, not a runner for AgentCore.run(): orchestration
has side effects outside boundaries. Only the pure router may run live in replay.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
from pathlib import Path
import re
from contextlib import contextmanager
from uuid import uuid4

import config

_log = logging.getLogger(__name__)
_active = contextvars.ContextVar("apex_incident_session", default=None)
_SECRET_FIELD = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)", re.I)
_ASSIGNMENT = re.compile(
    r"(?i)((?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*)([^\s,;]+)"
)


class RecordedBoundaryError(RuntimeError):
    """A recorded crossing failed; replay must not invent a successful result."""


def require_live_turn():
    """Block full orchestration in our replay scope before any side effect."""
    session = _active.get()
    if session is not None and session.mode == _library().SessionMode.REPLAY:
        raise RuntimeError("Full agent turns cannot run in boundary replay; call recorded boundaries only.")


def _library():
    import chronicle
    if chronicle.__version__ != "0.5.0":
        raise RuntimeError("Install requirements-research.txt: Chronicle 0.5.0 is required.")
    return chronicle


def _redact_text(text):
    # Include configured secrets with arbitrary formats, not just vendor patterns.
    from chronicle import redact_secrets
    text = redact_secrets()(text)
    secrets = [v for k, v in {**os.environ, **vars(config)}.items()
               if _SECRET_FIELD.search(k) and isinstance(v, str) and len(v) >= 4]
    for value in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return _ASSIGNMENT.sub(r"\1[REDACTED]", text)


def _sanitize(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if _SECRET_FIELD.search(str(k)) else _sanitize(v)
                for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        # Some tools encode their structured result as JSON text.
        import json
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return _redact_text(value)
        if isinstance(parsed, (dict, list)):
            return json.dumps(_sanitize(parsed), ensure_ascii=False)
        return _redact_text(value)
    return value


def boundary(name, *, kind="custom", tuple_result=False):
    """Lazy Chronicle boundary; no import or instrumentation outside our scope."""
    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            session = _active.get()
            if session is None:
                return fn(*args, **kwargs)
            chronicle = _library()
            called = False
            result = None
            original_error = None

            def invoke(*a, **kw):
                nonlocal called, result, original_error
                called = True
                try:
                    result = fn(*a, **kw)
                    return result
                except BaseException as exc:
                    original_error = exc
                    raise

            def capture(*a, **kw):
                bound = signature.bind(*a, **kw)
                bound.apply_defaults()
                return chronicle.Input(arguments=_sanitize(dict(bound.arguments)))

            def capture_result(value):
                cleaned = _sanitize(value)
                if tuple_result and kind == "router":
                    import json
                    return json.dumps(cleaned)
                # Chronicle's custom-boundary serializer stringifies lists.
                # An explicit object preserves tuple structure across fixtures.
                return {"__apex_tuple__": cleaned} if tuple_result else cleaned

            instrumented = chronicle.boundary(
                name, kind=kind, extract_input=capture, extract_result=capture_result
            )(invoke)
            replaying = session.mode == chronicle.SessionMode.REPLAY
            if replaying:
                index = session._replay_cursor.get(name, 0) + 1
                stub = session.replay_plan.should_stub(name, index)
                if not stub and name != "apex.route_model":
                    raise RuntimeError("Only the pure Apex router may execute live in replay.")
                if stub:
                    envelope = session.fixture_graph.envelope(name, index)
                    if envelope.status.code == "ERROR":
                        session._replay_cursor[name] = index
                        raise RecordedBoundaryError(envelope.status.message or "Recorded crossing failed")
            try:
                value = instrumented(*args, **kwargs)
            except Exception:
                if replaying:
                    raise  # Missing/malformed fixtures must never fall through live.
                if original_error is not None:
                    raise original_error
                _log.warning("Incident recording failed at %s; action is not retried", name)
                return result if called else fn(*args, **kwargs)
            if replaying and tuple_result:
                if kind == "router" and isinstance(value, str):
                    import json
                    return tuple(json.loads(value))
                if isinstance(value, dict) and "__apex_tuple__" in value:
                    return tuple(value["__apex_tuple__"])
            return value
        return wrapped
    return decorate


class _BestEffortStore:
    def __init__(self, path):
        self.store = _library().JsonlStore(path)

    def append(self, envelope):
        try:
            self.store.append(envelope)
        except Exception:
            _log.warning("Incident persistence failed; execution continues without retry")


@contextmanager
def record_turn(channel_id=None, *, store=None, export=None):
    """One local trace per turn. An explicit store enables test/manual recording.

    Nested turns share their enclosing trace. Failure to initialize recording
    does not prevent the turn. Failed turns remain in the append-only run file.
    """
    if _active.get() is not None:
        yield _active.get()
        return
    if store is None and not getattr(config, "INCIDENT_RECORDING_ENABLED", False):
        yield None
        return
    session_token = active_token = None
    try:
        chronicle = _library()
    except Exception:
        _log.warning("Incident recording unavailable; execution continues")
        yield None
        return
    if not chronicle.is_enabled():
        yield None
        return
    try:
        from chronicle import session as sessions
        # Upstream 0.5.0 context managers replace their ContextVar without
        # restoring it. Pin that API and restore the token on our side.
        session = chronicle.ChronicleSession()
        session.redactors = [_redact_text]
        session.begin_trace("apex-turn", attributes={
            "session_id": _redact_text(str(channel_id or "main")),
            "message_id": uuid4().hex,
        })
        path = Path(store) if store is not None else Path(config.INCIDENT_RECORDING_DIR) / f"{session.trace_id}.jsonl"
        session.store = _BestEffortStore(path)
        session_token = sessions._session.set(session)
        active_token = _active.set(session)
    except Exception:
        _log.warning("Incident recording unavailable; execution continues")
        if active_token is not None:
            _active.reset(active_token)
        if session_token is not None:
            sessions._session.reset(session_token)
        yield None
        return
    try:
        yield session
    finally:
        try:
            if export is not None:
                session.export_trace(export)
        except Exception:
            _log.warning("Incident fixture export failed")
        finally:
            _active.reset(active_token)
            sessions._session.reset(session_token)


@contextmanager
def replay(trace, *, live_router=False):
    """Replay boundary calls only. All effects/permission checks are stubbed.

    The only supported live cut-point is apex.route_model, a pure decision.
    Never call the full AgentCore.run() here: memory/telemetry outside recorded
    boundaries would still execute. Replay cannot be nested inside recording.
    """
    if _active.get() is not None:
        raise RuntimeError("Replay requires an isolated execution context.")
    chronicle = _library()
    from chronicle import session as sessions
    session = chronicle.ChronicleSession()
    session.load_trace(trace)
    plan = chronicle.ReplayPlan()
    if live_router:
        plan.live("apex.route_model")
    session.enable_replay(plan)
    session_token = sessions._session.set(session)
    active_token = _active.set(session)
    try:
        yield session
    finally:
        _active.reset(active_token)
        sessions._session.reset(session_token)
