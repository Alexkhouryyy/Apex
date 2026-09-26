"""Durable project recovery, conflicts, model provenance and untrusted input."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from agent import assembly, study_projects as projects
import config


@pytest.fixture(autouse=True)
def isolated(test_db):
    assembly._SESSIONS.clear()
    yield
    assembly._SESSIONS.clear()


def payload():
    s = assembly.create()
    sid = s['session_id']
    assembly.apply(sid, 'select', part='commutator')
    assembly.apply(sid, 'explode', amount=.8)
    assembly.apply(sid, 'section')
    s = assembly.apply(sid, 'isolate')
    return dict(name='Commutation study', model_hash=projects.model_hash(),
                session_id=sid, session_revision=s['revision'],
                workspace=dict(notes={'commutator': 'Why segmented? <script>literal</script>'},
                               camera={'position': [8, 4, 9], 'target': [.45, 0, 0]},
                               rotor_angle=1.25))


def test_project_survives_fresh_process_with_notes_view_and_camera(test_db):
    body = payload()
    p = projects.save(body)
    # A genuinely fresh interpreter has no session cache or module globals.
    code = '''import json,sys
from agent import longterm, study_projects
longterm.DB_PATH=sys.argv[1]
print(json.dumps(study_projects.open_project(sys.argv[2])))'''
    result = subprocess.run([sys.executable, '-c', code, test_db, p['id']],
                            capture_output=True, text=True, check=True)
    reopened = json.loads(result.stdout)
    s = reopened['state']
    assert s['session_id'] != body['session_id']
    assert s['selected'] == 'commutator' and s['explosion'] == .8
    assert s['isolated'] and s['section'] and not s['rotating']
    assert reopened['workspace'] == body['workspace']
    assert reopened['project']['version'] == 1
    assert projects.recent()[0]['compatible']


def test_atomic_versions_prevent_two_writers_from_losing_notes():
    body = payload(); p = projects.save(body)
    def write(label):
        b = deepcopy(body); b['version'] = 1;b['workspace']['notes']['commutator'] = label
        try:
            return projects.save(b, p['id'])
        except projects.Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ['first', 'second']))
    assert results.count('conflict') == 1
    assert projects.open_project(p['id'])['project']['version'] == 2
    # A stale writer can keep work by saving a new copy.
    assert projects.save(body)['id'] != p['id']


def test_model_revision_mismatch_preserves_old_project(monkeypatch):
    body = payload(); p = projects.save(body)
    original_hash = projects.model_hash
    monkeypatch.setattr(projects, 'model_hash', lambda: 'new-model-hash')
    assert not projects.recent()[0]['compatible']
    with pytest.raises(projects.Conflict, match='model revision'):
        projects.open_project(p['id'])
    with pytest.raises(projects.Conflict, match='model changed'):
        projects.save(body)
    body.update(model_hash='new-model-hash', version=1)
    with pytest.raises(projects.Conflict):projects.save(body, p['id'])
    monkeypatch.setattr(projects, 'model_hash', original_hash)
    assert projects.open_project(p['id'])['workspace']['notes'] == body['workspace']['notes']


def test_stale_view_and_invalid_workspace_rejected_without_writes():
    body = payload()
    assembly.apply(body['session_id'], 'assemble')
    with pytest.raises(projects.Conflict, match='view changed'):projects.save(body)
    for bad in ({'notes': {'invented': 'note'}},
                {'notes': {'commutator': 'x' * 8001}},
                {'camera': {'position': [float('nan'), 4, 9], 'target': [0, 0, 0]}},
                {'camera': {'position': [0, 0, 0], 'target': [0, 0, 0]}},
                {'rotor_angle': True}):
        b = payload();b['workspace'].update(bad)
        with pytest.raises(ValueError):projects.save(b)
    assert projects.recent() == []


def test_restore_is_independent_and_hidden_part_can_be_isolated():
    body = payload();p = projects.save(body)
    a, b = projects.open_project(p['id']), projects.open_project(p['id'])
    sid = a['state']['session_id']
    assembly.apply(sid, 'hide', part='shaft')
    s = assembly.apply(sid, 'isolate', part='shaft')
    assert s['selected'] == 'shaft' and 'shaft' not in s['hidden']
    assert assembly.state(b['state']['session_id'])['selected'] == 'commutator'
    assert assembly.apply(sid, 'undo')['selected'] is None


def test_project_routes_auth_origins_limits_and_status_codes(monkeypatch):
    from dashboard import server
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'study-project-test')
    with TestClient(server.app) as c:
        assert c.get('/api/study/projects').status_code == 401
        assert c.post('/api/study/projects/nope/open').status_code == 401
        c.headers['Authorization'] = 'Bearer study-project-test'
        body = payload()
        assert c.post('/api/study/projects', json=body, headers={'Origin': 'https://elsewhere.example'}).status_code == 403
        p = c.post('/api/study/projects', json=body).json()
        assert p['version'] == 1
        body['version'] = 1
        assert c.post('/api/study/projects/' + p['id'], json=body).json()['version'] == 2
        assert c.post('/api/study/projects/' + p['id'], json=body).status_code == 409
        assert c.post('/api/study/projects/' + p['id'] + '/open').status_code == 200
        assert c.post('/api/study/projects/nope/open').status_code == 404
        assert c.post('/api/study/projects', content='x' * 600001).status_code == 400
        assert c.post('/api/study/projects', json=[]).status_code == 400
        assert len(c.get('/api/study/projects').json()['projects']) == 1
