"""Phase 2b: the part an open hand points at reaches Céline with its age, so
"take this apart" means that part; stale or invalid pointing is dropped."""
import json

import pytest
from fastapi.testclient import TestClient

import config
from agent import assembly, study_input


@pytest.fixture(autouse=True)
def clean():
    assembly._SESSIONS.clear()
    study_input._lease = None
    yield
    assembly._SESSIONS.clear()
    study_input._lease = None


def test_pointing_is_remembered_with_its_age_then_forgotten():
    sid = assembly.create('jet-engine')['session_id']
    assert assembly.pointed(sid) is None
    assembly.point(sid, 'combustor', now=100.0)
    assert assembly.pointed(sid, now=103.04) == {'id': 'combustor', 'name': 'Combustion chamber (annular)', 'seconds_ago': 3.0}
    assert assembly.pointed(sid, now=100.0 + assembly.POINT_MEMORY_SECONDS + 0.1) is None, 'a minute-old point is not "this"'
    assembly.point(sid, 'invented-part', now=104.0)
    assembly.point(sid, None, now=104.0)
    assert assembly.pointed(sid, now=104.0)['id'] == 'combustor', 'invalid reports never replace a real point'
    other = assembly.create('heart')['session_id']
    assert assembly.pointed(other) is None, 'each study window has its own pointing'


def test_hand_samples_carry_the_pointed_part_into_celines_context(monkeypatch):
    from dashboard import server
    from dashboard.companion import workspace_message
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'point-test')
    with TestClient(server.app) as c:
        c.headers['Authorization'] = 'Bearer point-test'
        sid = c.post('/api/study/session', json={'model': 'car-engine'}).json()['session_id']
        owner = 'owner-for-pointing-test'
        hands = f'/api/study/session/{sid}/hands'
        assert c.post(hands, json={'action': 'claim', 'owner': owner}).status_code == 200
        assert c.post(hands, json={'action': 'sample', 'owner': owner, 'pointed': 'crankshaft'}).status_code == 200
        # Someone else's window cannot plant a pointed part.
        other = c.post('/api/study/session', json={'model': 'car-engine'}).json()['session_id']
        assert c.post(f'/api/study/session/{other}/hands', json={'action': 'sample', 'owner': 'another-owner-id-123', 'pointed': 'flywheel'}).status_code == 409
        assert assembly.pointed(other) is None
    prompt = workspace_message({'workspace': 'assembly', 'study_session': sid}, 'take this apart')
    context = json.loads(prompt.split('[Assembly study at send time: ', 1)[1].split(']\n', 1)[0])
    assert context['pointed_part']['id'] == 'crankshaft' and context['pointed_part']['seconds_ago'] < 5
    assert "'take this apart'" in prompt and 'select it and then explode' in prompt
    assert 'prefer it when recent' in prompt and 'ask which one' in prompt


def test_take_this_apart_is_select_then_explode_through_the_tool():
    from agent import core
    sid = assembly.create('heart')['session_id']
    assembly.point(sid, 'mitral-valve')
    part = assembly.context(sid)['pointed_part']['id']
    json.loads(core._execute_tool('assembly_study', {'action': 'select', 'session_id': sid, 'part': part}))
    state = json.loads(core._execute_tool('assembly_study', {'action': 'explode', 'session_id': sid}))
    assert state['selected'] == 'mitral-valve' and state['explosion'] == 1.0
