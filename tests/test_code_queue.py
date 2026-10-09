"""Portable queue acceptance: real Git worktrees, controlled agents, no paid calls."""
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from agent import code_studio as code, code_engines, work_engines
from tests.test_code_studio import lab  # temporary DB, vault and Git project; fixture works on Windows


def until(check):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.02)
    assert check(), 'Queue did not settle'


@pytest.fixture
def controlled(lab, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, active = [], []
    result = {'status': 'done', 'summary': 'Built'}

    def turn(engine, text, folder, mode, resume, on_event, run_id, **kwargs):
        assert not active, 'Coding turns must never overlap'
        active.append(run_id)
        calls.append((engine, text, kwargs['options']))
        try:
            if len(calls) == 1:
                entered.set()
                assert release.wait(60)
            (folder / f'change-{len(calls)}.txt').write_text('verified synthetic change')
            return dict(result)
        finally:
            active.remove(run_id)

    monkeypatch.setattr(code_engines, 'turn', turn)
    monkeypatch.setattr(work_engines, 'stop', lambda *a, **k: True)
    sid = code.start(lab.pid, 'Build from an idea')['id']
    assert entered.wait(15)
    yield sid, calls, release, result
    release.set()
    until(lambda: not code.session(sid)['working'] and not code.session(sid)['operation'])


def test_followups_run_in_order_once_with_selected_settings(controlled):
    sid, calls, release, _ = controlled
    opts = dict(engine='chatgpt', mode='safe', model='gpt-test', effort='high', request_id='same-request-01')
    code.enqueue(sid, 'Improve the existing feature', **opts)
    code.enqueue(sid, 'Improve the existing feature', **opts)  # retry after a lost HTTP response
    code.enqueue(sid, 'Add coverage', request_id='next-request-02')
    assert len(code.session(sid)['queue']['items']) == 2
    release.set()
    until(lambda: len(calls) == 3 and not code.session(sid)['working'] and not code.session(sid)['operation'])
    assert 'Improve the existing feature' in calls[1][1]
    assert 'Add coverage' in calls[2][1]
    assert calls[1][0] == 'chatgpt'
    assert calls[1][2]['model'] == 'gpt-test' and calls[1][2]['effort'] == 'high'
    assert code.session(sid)['queue']['items'] == []
    code.enqueue(sid, 'Improve the existing feature', **opts)
    assert len(calls) == 3, 'Retrying an already started item must not run it again'


@pytest.mark.parametrize('status', ['failed', 'limited', 'stopped'])
def test_failure_retains_pending_intent_without_autorun(controlled, status):
    sid, calls, release, result = controlled
    result['status'] = status
    code.enqueue(sid, 'Do not lose this', request_id='pending-request')
    release.set()
    until(lambda: not code.session(sid)['working'])
    q = code.session(sid)['queue']
    assert q['paused'] and len(q['items']) == 1
    assert len(calls) == 1
    assert code.events(sid)[-1]['kind'] == 'done', 'Queue state must be recorded before this step completes'


def test_stop_pauses_even_if_the_agent_reports_success(controlled):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'Next', request_id='pending-request')
    assert code.stop(sid)
    release.set()
    until(lambda: not code.session(sid)['working'])
    assert len(calls) == 1 and code.session(sid)['queue']['paused']
    code.resume_queue(sid)
    until(lambda: len(calls) == 2 and not code.session(sid)['working'])


def test_plan_boundary_requires_review_before_more_work(controlled):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'Plan the improvement', plan=True, request_id='plan-request-01')
    code.enqueue(sid, 'Then build', request_id='build-request-2')
    release.set()
    until(lambda: len(calls) == 2 and not code.session(sid)['working'])
    assert code.session(sid)['queue']['paused']
    assert len(code.session(sid)['queue']['items']) == 1


def test_allow_once_can_run_before_paused_followups(controlled):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'After the blocked command', request_id='after-allow-once')
    code.pause_queue(sid)
    release.set()
    until(lambda: not code.session(sid)['working'] and not code.session(sid)['operation'])
    code.allow(sid, 'echo hello')
    until(lambda: len(calls) == 2 and not code.session(sid)['working'] and not code.session(sid)['operation'])
    assert calls[1][2]['allow'] == ['echo hello']
    assert code.session(sid)['queue']['paused'] and len(code.session(sid)['queue']['items']) == 1


def test_restart_preserves_and_pauses_idle_pending_items(controlled):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'Pending on restart', request_id='restart-request')
    code.pause_queue(sid)
    release.set()
    until(lambda: not code.session(sid)['working'])
    code._set(sid, queue_paused=0)
    code._recover()
    q = code.session(sid)['queue']
    assert q['paused'] and 'restarted' in q['reason']
    assert q['items'][0]['prompt'] == 'Pending on restart'
    assert len(calls) == 1


def test_unsaved_checkpoint_does_not_dispatch_followup(controlled, monkeypatch):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'Only after a saved step', request_id='checkpoint-next')
    monkeypatch.setattr(code, '_wait_readable', lambda *a: ['locked.txt'])
    release.set()
    until(lambda: not code.session(sid)['working'])
    assert len(calls) == 1 and code.session(sid)['queue']['paused']
    assert 'checkpoint not saved' in code.session(sid)['queue']['reason']


def test_stop_during_followup_preparation_never_starts_the_model(controlled, monkeypatch):
    sid, calls, release, _ = controlled
    entered, finish = threading.Event(), threading.Event()
    original = code._brain
    def slow_brief(*args):
        entered.set()
        assert finish.wait(15)
        return original(*args)
    monkeypatch.setattr(code, '_brain', slow_brief)
    code.enqueue(sid, 'Waiting', request_id='slow-preparation')
    release.set()
    try:
        assert entered.wait(15)
        code.stop(sid)
    finally:
        finish.set()
    until(lambda: not code.session(sid)['operation'] and not code.session(sid)['working'])
    assert len(calls) == 1
    assert code.session(sid)['queue']['paused'] and len(code.session(sid)['queue']['items']) == 1


def test_inspection_failure_releases_the_turn_and_pauses_queue(controlled, monkeypatch):
    sid, calls, release, _ = controlled
    code.enqueue(sid, 'Only after inspection', request_id='inspection-next')
    def broken_count(*args):
        raise OSError('Drive unavailable')
    monkeypatch.setattr(code, '_count_changed', broken_count)
    release.set()
    until(lambda: not code.session(sid)['working'])
    s = code.session(sid)
    assert s['last_status'] == 'failed' and s['queue']['paused'] and len(calls) == 1


def test_queue_limits_removal_validation_and_operation_boundary(controlled):
    sid, _, _, _ = controlled
    with pytest.raises(code.CodeError):
        code.enqueue(sid, '', request_id='invalid-request')
    with pytest.raises(code.CodeError):
        code.enqueue(sid, 'Next', model='bad;command', request_id='invalid-request')
    for n in range(code.MAX_QUEUED):
        code.enqueue(sid, str(n), request_id=f'pending-{n:02}')
    with pytest.raises(code.CodeError, match='at most|At most'):
        code.enqueue(sid, 'Too many')
    code.pause_queue(sid)
    first = code.session(sid)['queue']['items'][0]['id']
    code.remove_queued(sid, first)
    assert len(code.session(sid)['queue']['items']) == code.MAX_QUEUED - 1


def test_queue_routes_require_owner_and_same_site(controlled, monkeypatch):
    from dashboard.code import router
    sid, _, _, _ = controlled
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'synthetic-owner-token')
    app = FastAPI()
    app.include_router(router)
    owner = {'enabled': False}
    @app.middleware('http')
    async def synthetic_owner(request, next_call):
        request.state.is_master = owner['enabled']
        return await next_call(request)

    with TestClient(app) as client:
        for action in ['queue', 'queue-pause', 'queue-resume', 'queue-remove']:
            assert client.post(f'/api/code/sessions/{sid}/{action}', json={'prompt': 'Next'}).status_code == 403

    owner['enabled'] = True
    with TestClient(app) as client:
        response = client.post(f'/api/code/sessions/{sid}/queue', json={'prompt': 'Next', 'request_id': 'route-request'})
        assert response.status_code == 200 and len(response.json()['items']) == 1
        assert client.post(f'/api/code/sessions/{sid}/queue', headers={'Origin': 'https://untrusted.example'}, json={'prompt': 'Next'}).status_code == 403
