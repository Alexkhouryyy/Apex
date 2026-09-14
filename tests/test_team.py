"""Offline contract tests for durable execution, handoffs and enforced limits."""
import json
import threading
from types import SimpleNamespace as NS

import pytest
import config
from agent import team, subagent_scope


def text_response(text='Finished', calls=()):
    return NS(content=[NS(type='text', text=text), *[NS(type='tool_use', id=str(i), name=n, input=a) for i,(n,a) in enumerate(calls)]], stop_reason='tool_use' if calls else 'end_turn', usage=NS(input_tokens=100, output_tokens=100))


@pytest.fixture
def setup(test_db, monkeypatch):
    monkeypatch.setattr(config, 'DEEPSEEK_API_KEY', 'fake-local-test')
    monkeypatch.setattr(config, 'AGENT_MODEL', 'deepseek-flash')
    monkeypatch.setattr(team, '_ready', None)
    monkeypatch.setattr(team, '_active', {})
    monkeypatch.setattr(team.budget, 'check', lambda: None)
    monkeypatch.setattr(team.provider, 'get_client', lambda model: object())
    # Do not start actual threads: run synchronously and inspect each checkpoint.
    monkeypatch.setattr(team, 'Thread', lambda **kwargs: NS(start=lambda:None))
    agent=NS(_all_tools=lambda: [{'name':n} for n in ['read_file','write_file','bash','web_search','spawn_subagent']])
    spec=dict(id='test_task_identifier_123', task='Fix project', context='Project folder and explicit acceptance checks', models={}, budget_usd=.5)
    return agent, spec


def test_handoffs_and_independent_review(setup, monkeypatch, tmp_path):
    from agent import core
    agent, spec = setup
    target=tmp_path/'result.txt'
    models=[]
    counts={}
    def create(client, **kw):
        role=kw['call_site'].split('/')[-1]
        models.append(kw['model'])
        counts[role]=counts.get(role,0)+1
        user=json.loads(kw['messages'][0]['content'])
        assert user['project_context']==spec['context']
        offered={t['name'] for t in kw.get('tools',[])}
        assert 'spawn_subagent' not in offered
        if role=='coder':
            assert user['previous_results'][0]['role']=='researcher'
            if counts[role]==1:
                return text_response(calls=[('write_file',{'path':str(target),'content':'fixed'})])
        if role=='reviewer':
            assert user['previous_results'][1]['role']=='coder'
            assert not {'bash','write_file'} & offered
            if counts[role]==1:
                return text_response(calls=[('read_file',{'path':str(target)})])
        return text_response(role+' report')
    def execute(name, args):
        assert subagent_scope.check(name) is None
        if name=='write_file':
            target.write_text(args['content']); return 'Wrote file'
        assert target.read_text()=='fixed'
        return target.read_text()
    monkeypatch.setattr(team.telemetry,'create',create)
    monkeypatch.setattr(core,'_execute_tool',execute)
    data=team.submit(spec,agent)
    team.run(data,agent,threading.Event())
    saved=team.get(data['id'])
    assert saved['status']=='done'
    assert all(s['status']=='done' for s in saved['steps'])
    assert saved['cost_usd']>0
    assert saved['steps'][2]['evidence'][0]['result']=='fixed'
    assert saved['steps'][-1]['result']=='apex report'
    assert subagent_scope.active_role() is None
    assert set(models)=={'deepseek-flash'}


def test_unoffered_tool_never_dispatches(setup, monkeypatch):
    from agent import core
    agent,spec=setup
    spec['roles']=['reviewer']
    responses=iter([text_response(calls=[('write_file',{})]),text_response(),text_response()])
    monkeypatch.setattr(team.telemetry,'create',lambda *a,**k:next(responses))
    monkeypatch.setattr(core,'_execute_tool',lambda *a:pytest.fail('Unauthorized dispatch'))
    data=team.submit(spec,agent); team.run(data,agent,threading.Event())
    assert '[BLOCKED]' in team.get(data['id'])['steps'][0]['evidence'][0]['result']


def test_restart_never_replays_and_retry_is_idempotent(setup, monkeypatch):
    agent,spec=setup
    first=team.submit(spec,agent)
    assert team.submit(spec,agent)['id']==first['id']
    with pytest.raises(ValueError):
        team.submit({**spec,'task':'Different action'},agent)
    with pytest.raises(RuntimeError):
        team.submit({**spec,'id':'different_task_id_456'},agent)
    monkeypatch.setattr(team,'_ready',None)
    restored=team.get(first['id'])
    assert restored['status']=='interrupted'
    assert all(s['status']=='skipped' for s in restored['steps'])


@pytest.mark.parametrize('reason',['cancel','budget','failure','limit'])
def test_stop_budget_failure_and_exhaustion_skip_dependents(setup, monkeypatch, reason):
    from agent import core
    agent,spec=setup
    data=team.submit(spec,agent)
    cancel=threading.Event()
    calls=[]
    def create(*a,**kw):
        calls.append(kw)
        if reason=='cancel': cancel.set()
        if reason=='failure': raise RuntimeError('Fake provider failure')
        if reason=='budget': data['cost_usd']=spec['budget_usd']
        return text_response(calls=[('read_file',{})])
    monkeypatch.setattr(team.telemetry,'create',create)
    monkeypatch.setattr(core,'_execute_tool',lambda *a:'read evidence')
    team.run(data,agent,cancel)
    saved=team.get(data['id'])
    assert saved['status'] in {'blocked','interrupted','failed'}
    assert all(s['status']=='skipped' for s in saved['steps'][1:])
    assert len(calls)==(4 if reason=='limit' else 1)
    assert data['id'] not in team._active
    assert subagent_scope.active_role() is None


def test_model_choices_and_validation(setup):
    _,spec=setup
    spec['models']={'coder':'deepseek-v4.1-flash'}
    assert team.validate(spec)['models']['coder']=='deepseek-flash'
    for invalid in [float('nan'),float('inf'),0,6,True]:
        with pytest.raises(ValueError): team.validate({**spec,'budget_usd':invalid})
    with pytest.raises(ValueError): team.validate({**spec,'models':{'coder':'unknown-paid-model'}})


def test_routes_enforce_origin_auth_and_size(setup, monkeypatch):
    from dashboard import server
    from fastapi.testclient import TestClient
    agent,spec=setup
    monkeypatch.setattr(server,'_agent_ref',agent)
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','test-token')
    client=TestClient(server.app)
    assert client.get('/api/team').status_code==401
    h={'Authorization':'Bearer test-token'}
    assert client.post('/api/team',json=spec,headers={**h,'Origin':'https://other.invalid'}).status_code==403
    assert client.post('/api/team',content='x'*30001,headers=h).status_code==413
    assert client.post('/api/team',json=spec,headers=h).status_code==200
    result=client.get('/api/team',headers=h).json()['runs'][0]
    assert result['id']==spec['id']
    assert client.post('/api/team/'+spec['id']+'/stop',json={},headers=h).json()['stop_requested']


def test_chat_tools_use_same_durable_runner(setup, monkeypatch):
    from agent import core
    from dashboard import server
    agent, spec = setup
    agent._model = 'deepseek-flash'
    monkeypatch.setattr(server, '_agent_ref', agent)
    monkeypatch.setattr(core.safety, 'check', lambda *a: (True, ''))
    result=json.loads(core._execute_tool_inner('start_team_task',spec))
    assert result['id']==spec['id']
    saved=json.loads(core._execute_tool_inner('team_task_status',{'id':result['id']}))
    assert saved['context']==spec['context']
    assert set(saved['models'].values())=={'deepseek-flash'}
    assert json.loads(core._execute_tool_inner('stop_team_task',{'id':result['id']}))['stop_requested']
    subagent_scope.set_active('team_coder')
    try:
        assert '[Blocked]' in core._execute_tool_inner('start_team_task',spec)
    finally:
        subagent_scope.clear_active()


def test_team_search_never_calls_hidden_anthropic(setup, monkeypatch):
    from tools import research
    import ddgs
    monkeypatch.setattr(config,'BACKGROUND_MODEL','claude-sonnet-5')
    monkeypatch.setattr(config,'ANTHROPIC_API_KEY','fake')
    monkeypatch.setattr(research,'_search_via_anthropic',lambda *a:pytest.fail('Hidden billable call'))
    monkeypatch.setattr(ddgs,'DDGS',lambda:NS(text=lambda *a,**k:[{'title':'source','href':'https://example.test','body':'evidence'}]))
    subagent_scope.set_active('team_researcher')
    try:
        assert research.search('query')[0]['title']=='source'
    finally:
        subagent_scope.clear_active()


def test_restart_marks_inflight_tool_outcome_unknown(setup, monkeypatch):
    agent, spec=setup
    data=team.submit(spec,agent)
    data['status']='running'
    data['steps'][0]['status']='running'
    data['steps'][0]['evidence']=[{'tool':'read_file','status':'running','result':''}]
    team.save(data)
    monkeypatch.setattr(team,'_ready',None)
    restored=team.get(data['id'])
    assert restored['steps'][0]['status']=='interrupted'
    assert restored['steps'][0]['evidence'][0]['status']=='outcome_unknown'
