"""Remote disconnect/retry semantics and gesture-to-voice object identity."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import config
from agent import board, companion_jobs as jobs, conversations
from agent.memory import Memory
from dashboard import companion as routes, server


class Worker:
    def __init__(self):
        self.calls = []
        self.channels = {}
        self.entered = threading.Event()
        self.release = threading.Event()

    def _get_channel(self, key):
        return self.channels.setdefault(key, (Memory(), threading.Lock()))

    def run(self, message, **kwargs):
        self.calls.append((message, kwargs))
        self.entered.set()
        assert self.release.wait(5), 'test did not release the worker'
        kwargs['streamer'].feed('Verified result')
        kwargs['streamer'].tool({'type': 'tool', 'phase': 'result', 'name': 'test', 'result': '1 passed'})
        return 'Verified result'


@pytest.fixture
def remote(test_db, monkeypatch):
    worker = Worker()
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'remote-test-token')
    monkeypatch.setattr(server, '_agent_ref', worker)
    monkeypatch.setattr(board, 'get_board', lambda: local_board)
    local_board = board.Board()
    routes._active.clear()
    routes._threads.clear()
    with TestClient(server.app) as client:
        client.headers['Authorization'] = 'Bearer remote-test-token'
        yield client, worker, local_board
        worker.release.set()
        deadline = time.monotonic() + 5
        while routes._active and time.monotonic() < deadline:
            time.sleep(.01)


def payload(**changes):
    return {'turn_id': 'remote-task-123456789', 'message': 'Run my checks', 'mode': 'work', **changes}


def finished(client, turn_id='remote-task-123456789'):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get('/api/companion/jobs/' + turn_id).json()
        if data['status'] != 'running':
            return data
        time.sleep(.01)
    pytest.fail('task did not finish')


def test_remote_shell_public_data_and_actions_authenticated(remote):
    client, _, _ = remote
    client.headers.clear()
    assert client.get('/drive').status_code == 200
    for url in ['/api/companion/jobs', '/api/companion/jobs/unknown', '/api/forge/exports', '/api/forge/download/test.stl']:
        assert client.get(url).status_code == 401
    assert client.post('/api/companion/jobs', json=payload()).status_code == 401
    assert client.post('/api/board/select', json={'id': None}).status_code == 401


def test_returning_from_request_does_not_cancel_worker_and_retry_is_idempotent(remote):
    client, worker, _ = remote
    result = client.post('/api/companion/jobs', json=payload())
    assert result.status_code == 202
    assert worker.entered.wait(2)
    assert not worker.calls[0][1]['cancel_event'].is_set()
    retry = client.post('/api/companion/jobs', json=payload())
    assert retry.status_code == 202
    assert retry.json()['thread_id'] == result.json()['thread_id']
    assert 'fingerprint' not in retry.json()
    assert len(worker.calls) == 1
    assert client.post('/api/companion/jobs', json=payload(message='Different action')).status_code == 409
    worker.release.set()
    data = finished(client)
    assert data['status'] == 'done' and data['text'] == 'Verified result'
    assert data['evidence'][0]['result'] == '1 passed'
    assert client.get('/api/companion/jobs').json()['jobs'][0]['id'] == data['id']
    assert len(conversations.messages(data['thread_id'])) == 2
    assert client.post('/api/companion/jobs', json=payload()).status_code == 202
    assert len(worker.calls) == 1


def test_remote_and_streaming_share_busy_guard_and_cancel(remote):
    client, worker, _ = remote
    thread = client.post('/api/companion/jobs', json=payload()).json()['thread_id']
    assert worker.entered.wait(2)
    other = payload(turn_id='another-turn-123456789', thread_id=thread)
    for url in ['/api/companion/chat', '/api/companion/jobs']:
        assert client.post(url, json=other).status_code == 409
    assert client.post('/api/companion/cancel/remote-task-123456789').json()['cancel_requested']
    worker.release.set()
    assert finished(client)['status'] == 'interrupted'


def test_restart_marks_uncertain_work_without_replaying(test_db, monkeypatch):
    jobs.create('lost-task', 1, 'hash', 'Change a file')
    jobs.update('lost-task', text='Partial evidence', evidence=[])
    monkeypatch.setattr(jobs, '_ready_for', None)  # fresh process
    recovered = jobs.get('lost-task')
    assert recovered['status'] == 'interrupted'
    assert recovered['text'] == 'Partial evidence'
    assert 'before retrying' in recovered['error']


def test_failed_remote_task_has_retrievable_error(remote, monkeypatch):
    client, worker, _ = remote
    def fail(*args, **kwargs):
        raise RuntimeError('Provider unavailable')
    monkeypatch.setattr(worker, 'run', fail)
    assert client.post('/api/companion/jobs', json=payload()).status_code == 202
    data = finished(client)
    assert data['status'] == 'failed'
    assert 'Provider unavailable' in data['error']


def test_cross_origin_remote_and_selection_rejected(remote, monkeypatch):
    client, worker, _ = remote
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', '')
    for path in ['/api/companion/jobs', '/api/board/select']:
        assert client.post(path, json=payload(), headers={'Origin': 'https://evil.example'}).status_code == 403
    assert not worker.calls


def test_voice_captures_selected_identity_even_when_selection_changes(remote):
    client, worker, local = remote
    first = local.add('model', 'First', src='first.glb')
    second = local.add('model', 'Second', src='second.glb')
    assert client.post('/api/board/select', json={'id': first.id}).status_code == 200
    assert client.post('/api/companion/jobs', json=payload(workspace='board')).status_code == 202
    assert worker.entered.wait(2)
    local.select(second.id)
    message = worker.calls[0][0]
    assert first.id in message and 'first.glb' in message
    assert second.id not in message
    worker.release.set()
    finished(client)


def test_hand_selection_persists_on_release_and_deleted_target_clears():
    local = board.Board()
    obj = local.add('model', 'A model', x=.5, y=.5)
    local.apply_hands([(.5, .5, True)], now=1)
    local.apply_hands([(.5, .5, True)], now=1.2)
    assert local.selection()['id'] == obj.id
    local.apply_hands([], now=2)
    assert local.selection()['id'] == obj.id
    local.remove(obj.id)
    assert local.selection() is None


def test_view_transform_undo_and_invalid_values():
    local = board.Board()
    obj = local.add('model', 'A model')
    local.transform(obj.id, scale=2, rot=1.5)
    local.undo()
    assert obj.scale == 1 and obj.rot == 0
    local.redo()
    assert obj.scale == 2 and obj.rot == 1.5
    for changes in [{'scale': float('nan')}, {'x': 2}, {'scale': True}, {'depth': 4}]:
        with pytest.raises(ValueError):
            local.transform(obj.id, **changes)
    obj.held_by = [0]
    with pytest.raises(ValueError, match='Release'):
        local.transform(obj.id, scale=1)


def test_export_download_is_jailed_and_exact(remote, tmp_path, monkeypatch):
    from agent import props
    client, _, _ = remote
    root = tmp_path / 'props'
    root.mkdir()
    monkeypatch.setattr(props, 'props_root', lambda: root)
    (root / 'part.stl').write_bytes(b'actual export')
    (root / 'secret.txt').write_text('private')
    outside = tmp_path / 'outside.stl'
    outside.write_bytes(b'outside')
    (root / 'escape.stl').symlink_to(outside)
    assert client.get('/api/forge/exports').json()['files'] == ['part.stl']
    response = client.get('/api/forge/download/part.stl')
    assert response.content == b'actual export'
    assert 'attachment' in response.headers['content-disposition']
    for path in ['secret.txt', 'escape.stl', '%2e%2e/outside.stl']:
        assert client.get('/api/forge/download/' + path).status_code == 404


def test_selection_and_workspace_validation(remote):
    client, worker, _ = remote
    for body in [{'id': 'missing'}, {'id': []}, []]:
        assert client.post('/api/board/select', json=body).status_code == 400
    assert client.post('/api/companion/jobs', json=payload(workspace='unknown')).status_code == 400
    assert client.get('/api/companion/jobs/missing').status_code == 404
    assert not worker.calls
