"""Assembly study sessions, truthful model data, reversible views and auth."""
import pytest
from fastapi.testclient import TestClient
from agent import assembly
import config

@pytest.fixture(autouse=True)
def clean_sessions():
    assembly._SESSIONS.clear()
    yield
    assembly._SESSIONS.clear()


def test_component_ids_and_sources_are_consistent():
    m=assembly.model()
    ids=[p['id'] for p in m['parts']]
    assert len(ids)==len(set(ids))==14
    refs={s['id'] for s in m['sources']}
    assert all(p['source'] in refs for p in m['parts'])
    assert 'not to scale' in m['fidelity']
    assert 'No manufacturer dimensions' in m['limitations']


def test_study_operations_are_reversible_and_sessions_are_independent():
    a,b=assembly.create(),assembly.create()
    sid=a['session_id']
    assembly.apply(sid,'explode',amount=.7)
    assembly.apply(sid,'select',part='commutator')
    assert assembly.apply(sid,'isolate')['isolated']
    with pytest.raises(ValueError,match='Reassemble'):assembly.apply(sid,'rotate')
    assert assembly.apply(sid,'hide')['hidden']==['commutator']
    restored=assembly.apply(sid,'assemble')
    assert restored['explosion']==0 and not restored['hidden'] and not restored['isolated']
    assert assembly.apply(sid,'undo')['hidden']==['commutator']
    assert assembly.apply(sid,'redo')['hidden']==[]
    assert assembly.state(b['session_id'])==b
    assert assembly.apply(sid,'select',part='Output shaft')['selected']=='shaft'
    assert assembly.context(sid)['selected_part']['id']=='shaft'


def test_rejected_edits_do_not_mutate_history_and_sessions_are_bounded():
    s=assembly.create();sid=s['session_id']
    for amount in (True,2,-1,float('nan'),'1'):
        with pytest.raises(ValueError):assembly.apply(sid,'explode',amount=amount)
        assert assembly.state(sid)==s
    with pytest.raises(ValueError):assembly.apply(sid,'select',part='<script>')
    with pytest.raises(ValueError):assembly.model('../secrets')
    for _ in range(assembly.MAX_SESSIONS):assembly.create()
    with pytest.raises(ValueError,match='expired'):assembly.state(sid)
    assert len(assembly._SESSIONS)==assembly.MAX_SESSIONS


def test_routes_enforce_auth_origin_and_session_context(monkeypatch):
    from dashboard import server
    from dashboard.companion import workspace_message
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','study-test')
    with TestClient(server.app) as c:
        assert c.get('/study').status_code==200
        assert c.get('/api/study/model/dc-motor').status_code==401
        c.headers['Authorization']='Bearer study-test'
        assert c.post('/api/study/session',json={},headers={'Origin':'https://other.example'}).status_code==403
        s=c.post('/api/study/session',json={}).json();sid=s['session_id']
        assert c.post('/api/study/session/'+sid,json={'action':'select','part':'commutator'}).status_code==200
        prompt=workspace_message({'workspace':'assembly','study_session':sid},'Explain this')
        assert 'Segmented commutator' in prompt and sid in prompt
        assert 'do not invent' in prompt and 'simplified illustration' in prompt
        with pytest.raises(ValueError,match='expired'):workspace_message({'workspace':'assembly','study_session':'bad'},'Explain')
        assert c.post('/api/study/session/'+sid,json={'action':'explode','amount':3}).status_code==400
        assert c.get('/api/study/session/bad').status_code==404


def test_voice_tool_uses_the_selected_study_session(monkeypatch):
    from agent import core, companion
    s=assembly.create();sid=s['session_id']
    assert 'assembly_study' in companion.DISCUSS_TOOLS
    result=core._execute_tool('assembly_study',{'action':'explode','session_id':sid})
    assert '"explosion": 1.0' in result
    assert 'not applied' in core._execute_tool('assembly_study',{'action':'select','session_id':sid,'part':'invented'})
