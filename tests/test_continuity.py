import base64
import json
from types import SimpleNamespace as NS
import pytest
import config
from agent import board, board_workspaces, continuity, skill_imports, skill_md, team, task_recovery


@pytest.fixture
def isolated(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(board, '_board', None)
    monkeypatch.setattr(board_workspaces, '_boards', {})
    monkeypatch.setattr(team, '_ready', None)
    monkeypatch.setattr(team, '_active', {})
    monkeypatch.setattr(skill_md, '_SKILLS_DIR', tmp_path/'skills')
    monkeypatch.setattr(skill_md, '_USAGE_FILE', tmp_path/'skills'/'.usage.json')
    board.get_board()
    return tmp_path


def test_identity_project_corrections_survive_restart_and_retire(isolated, monkeypatch):
    continuity.save_identity({**continuity.DEFAULT_IDENTITY, 'address':'Alex', 'preferences':'Explain tradeoffs first.'}, 0)
    p = continuity.project('default')
    continuity.save_project('default', {**p['data'],'next_step':'Validate the motor geometry'}, 0)
    continuity.save_corrections('default', [dict(text='Use millimeters for dimensions.', active=True)], 0)
    monkeypatch.setattr(board, '_board', None)
    for channel in (None, 'dashboard:123', 'companion:456'):
        with continuity.turn(channel):
            assert 'Alex' in continuity.persona_block()
            assert 'motor geometry' in continuity.prompt()
            assert 'millimeters' in continuity.prompt()
    continuity.save_corrections('default', [dict(text='Use millimeters for dimensions.', active=False)], 1)
    with continuity.turn(None):
        assert 'millimeters' not in continuity.prompt()
    with continuity.turn('sms:123'):
        assert continuity.persona_block() is None
        assert not continuity.prompt()
        with pytest.raises(ValueError):
            continuity.checkpoint(p['data'], 1)


def test_turn_is_pinned_and_stale_save_refused(isolated):
    with continuity.turn('dashboard:10'):
        second=board_workspaces.create('Project two', False, board.get_board().workspace_context())
        board_workspaces.switch(second['id'], board.get_board().workspace_context())
        continuity.checkpoint(dict(brief='first project',decisions='',artifacts='',next_step=''),0)
        assert 'first project' in continuity.prompt()
        with pytest.raises(board_workspaces.Conflict):
            continuity.checkpoint(dict(brief='stale',decisions='',artifacts='',next_step=''),0)
        current=continuity.checkpoint(action='read')
        assert current['revision']==1 and current['data']['brief']=='first project'
    assert not continuity.project(second['id'])['data']['brief']
    with continuity.turn('dashboard:10'):
        assert 'first project' in continuity.prompt()
    with continuity.turn('companion:10'):
        assert 'first project' in continuity.prompt()
    with continuity.turn('dashboard:11'):
        assert second['id'] in continuity.prompt()


def test_profile_reaches_every_persona_and_next_turn(isolated, monkeypatch):
    from agent import core, goals
    goals.init_db()
    monkeypatch.setattr(config,'ANTHROPIC_API_KEY','fake')
    monkeypatch.setattr(config,'JARVIS_PERSONA_ENABLED',True)
    agent=core.AgentCore()
    assert agent._get_channel('dashboard:12') == agent._get_channel('companion:12')
    assert agent._get_channel('dashboard:13') != agent._get_channel('companion:12')
    continuity.save_identity({**continuity.DEFAULT_IDENTITY,'address':'Alexandra'},0)
    for persona in (None,'celine'):
        with continuity.turn('companion:12'):
            joined=str(agent._effective_system_prompt(persona))
            assert 'Alexandra' in joined and 'CURRENT PROJECT HANDOFF' in joined
    assert 'Address the user as "sir"' not in joined


def fake_github(repo, revision, path):
    files={'skills/demo/SKILL.md':'---\nname: upstream-name\ndescription: A reviewed workflow\n---\nRead refs/check.md',
           'skills/demo/refs/check.md':'Verify the output before claiming completion.', 'LICENSE':'MIT license fixture'}
    if path=='skills/demo':
        return [dict(type='file',path=path+'/SKILL.md'),dict(type='dir',path=path+'/refs')]
    if path=='skills/demo/refs':
        return [dict(type='file',path=path+'/check.md')]
    return dict(type='file',encoding='base64',content=base64.b64encode(files[path].encode()).decode())


def test_review_preserves_support_files_license_and_checks_tampering(isolated, monkeypatch):
    monkeypatch.setattr(skill_imports,'_github',fake_github)
    p=skill_imports.preview('hermes','a'*40,'skills/demo')
    assert not skill_md.list_skills()
    installed=skill_imports.install(p['id'],'hermes-demo','Uses read_file; supported on Windows. No extra dependencies.')
    root=skill_md._SKILLS_DIR/'hermes-demo'
    assert (root/'refs/check.md').exists() and (root/'UPSTREAM-LICENSE.txt').exists()
    assert installed['revision']=='a'*40
    assert skill_md.list_skills()[0]['name']=='hermes-demo'
    skill_imports.set_enabled('hermes-demo',False)
    assert not skill_md.list_skills()
    assert 'disabled' in skill_md.manage('view',name='hermes-demo')
    skill_imports.set_enabled('hermes-demo',True)
    assert skill_md.list_skills()
    (root/'refs/check.md').write_text('unreviewed change')
    assert not skill_md.list_skills()
    assert not skill_imports.set_enabled('hermes-demo',True)['available']
    assert 'immutable' in skill_md.manage('patch',name='hermes-demo',old_text='Read',new_text='Execute')


def test_script_import_and_path_traversal_refused(isolated, monkeypatch):
    def upstream(repo,revision,path):
        value=fake_github(repo,revision,path)
        if path=='skills/demo':value.append(dict(type='file',path='skills/demo/run.py'))
        return value
    monkeypatch.setattr(skill_imports,'_github',upstream)
    p=skill_imports.preview('openclaw','b'*40,'skills/demo')
    with pytest.raises(ValueError):skill_imports.install(p['id'],'demo','Reviewed all requirements and dependencies.')
    with pytest.raises(ValueError):skill_imports.preview('openclaw','main','skills/demo')
    with pytest.raises(ValueError):skill_imports.preview('hermes','a'*40,'skills/../../private')
    with pytest.raises(ValueError):skill_imports.install(p['id'],'../../bad','Reviewed all requirements and dependencies.')


def prepare_run(monkeypatch):
    monkeypatch.setattr(config,'DEEPSEEK_API_KEY','fake')
    monkeypatch.setattr(config,'AGENT_MODEL','deepseek-flash')
    monkeypatch.setattr(team,'Thread',lambda **kw:NS(start=lambda:None))
    agent=NS(_all_tools=lambda:[])
    run=team.submit(dict(id='original_run_123456', task='Build something',models={},budget_usd=.5),agent)
    run['steps'][0].update(status='done',result='Research completed')
    run['steps'][1].update(status='running', evidence=[dict(tool='write_file',status='running',result='',input='target.txt')])
    team.save(run)
    # Simulate process death: no tool is executed when the new process reads it.
    monkeypatch.setattr(team,'_ready',None)
    monkeypatch.setattr(team,'_active',{})
    return team.get(run['id']),agent


def test_recovery_requires_reconciliation_and_never_replays_on_restart(isolated, monkeypatch):
    run,agent=prepare_run(monkeypatch)
    assert run['status']=='interrupted'
    assert run['steps'][1]['evidence'][0]['status']=='outcome_unknown'
    with pytest.raises(ValueError):task_recovery.continue_run(run['id'],'Only run the remaining tests',.5,agent)
    with pytest.raises(ValueError):task_recovery.review(run['id'],'Inspected the current output files.',{})
    task_recovery.review(run['id'],'The file exists with the intended content. Tests remain.',{'1:0':'Inspected target.txt; the write occurred and its content is correct.'})
    child=task_recovery.continue_run(run['id'],'Only run the remaining tests',.5,agent)
    assert child['recovery']['parent_id']==run['id']
    assert child['tools_used']==0 and child['calls']==0
    assert 'Never replay' in child['context'] and 'write_file' in child['context']
    assert task_recovery.continue_run(run['id'],'Only run the remaining tests',.5,agent)['id']==child['id']
    assert team.get(run['id'])==run


def test_home_owner_routes_and_stale_writes(isolated, monkeypatch):
    from dashboard import server
    from fastapi.testclient import TestClient
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','owner-test')
    c=TestClient(server.app)
    assert c.get('/home').status_code==200
    assert c.get('/api/home').status_code==401
    c.headers['Authorization']='Bearer owner-test'
    assert c.get('/api/home').status_code==200
    d=dict(data=continuity.DEFAULT_IDENTITY,revision=0)
    assert c.post('/api/home/identity',json=d,headers={'Origin':'https://other.test'}).status_code==403
    assert c.post('/api/home/identity',json=d).status_code==200
    assert c.post('/api/home/identity',json=d).status_code==409
    assert c.post('/api/home/identity',content='x'*24001).status_code==413


def test_learning_requires_verified_run_and_stages_approval(isolated, monkeypatch):
    from dashboard.home import propose
    from agent import approvals
    run,agent=prepare_run(monkeypatch)
    with pytest.raises(ValueError):propose(run['id'],'demo','Useful procedure','Inspect files and verify the expected content.')
    run['status']='done';run['verification']['status']='verified';team.save(run)
    captured=[]
    monkeypatch.setattr(approvals,'stage',lambda kind,payload:captured.append((kind,payload)) or 'Pending approval')
    result=propose(run['id'],'demo','Useful procedure','Inspect files and verify the expected content.')
    assert result['result']=='Pending approval' and captured[0][0]=='skill'
    assert run['id'] in captured[0][1]['content']
    assert not skill_md.list_skills()


def test_main_voice_restores_text_and_keeps_projects_separate(isolated):
    from agent.memory import Memory
    first=NS(memory=Memory())
    with continuity.turn(None), continuity.conversation(None,first,first.memory,'Remember the motor design') as memory:
        memory.add_user('Remember the motor design')
        memory.add_assistant([dict(type='text',text='We will check clearance next.')])
    restarted=NS(memory=Memory())
    with continuity.turn(None), continuity.conversation(None,restarted,restarted.memory,'Continue') as memory:
        assert 'clearance' in str(memory.messages)
        memory.add_user('Continue')
        memory.add_assistant([dict(type='text',text='Checking the clearance.')])
    second=board_workspaces.create('Other project',False,board.get_board().workspace_context())
    board_workspaces.switch(second['id'],board.get_board().workspace_context())
    with continuity.turn(None), continuity.conversation(None,restarted,restarted.memory,'New project') as memory:
        assert not memory.messages
        memory.add_user('New project')
        memory.add_assistant([dict(type='text',text='Starting the second project.')])


def test_auto_generated_code_proposal_is_reviewed_not_installed(isolated, monkeypatch):
    from agent import core, skills, approvals
    approvals.init_db()
    monkeypatch.setattr(skills,'SKILLS_DIR',isolated/'code-skills')
    monkeypatch.setattr(skills,'list_skills',lambda:[])
    monkeypatch.setattr(skills,'discover',lambda:{})
    monkeypatch.setattr(core.telemetry,'create',lambda *a,**kw:NS(content=[NS(type='text',text=json.dumps(dict(create=True,name='proposal',description='Example',code="def run(inputs):\n    return 'ok'")))]))
    core._propose_skill(object(),'A task',['read_file','write_file'])
    assert not (skills.SKILLS_DIR/'proposal.py').exists()
    assert any(p['kind']=='skill_code' for p in approvals.list_pending())


def test_reviewed_bundles_install_intact_and_keep_disabled_state(isolated, monkeypatch):
    monkeypatch.setattr(skill_imports,'_github',fake_github)
    p=skill_imports.preview('hermes','a'*40,'skills/demo')
    skill_imports.install(p['id'],'hermes-demo','Uses read_file and local files. No extra dependencies.')
    bundled=skill_md._SKILLS_DIR
    runtime=isolated/'runtime-skills'
    monkeypatch.setattr(skill_md,'_BUNDLED_DIR',bundled)
    monkeypatch.setattr(skill_md,'_SKILLS_DIR',runtime)
    monkeypatch.setattr(skill_md,'_USAGE_FILE',runtime/'.usage.json')
    assert skill_md.install_bundled()==1
    assert (runtime/'hermes-demo/refs/check.md').is_file()
    assert skill_imports.available(runtime/'hermes-demo')
    assert 'APEX COMPATIBILITY REVIEW' in skill_md.manage('view',name='hermes-demo')
    path=runtime/'hermes-demo/SKILL.md'
    path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
    assert skill_imports.available(runtime/'hermes-demo')
    skill_imports.set_enabled('hermes-demo',False)
    assert skill_md.install_bundled()==0
    assert not skill_imports.available(runtime/'hermes-demo')


def test_dashboard_rehydrates_saved_thread_after_restart(isolated, monkeypatch):
    from agent import conversations
    from agent.memory import Memory
    from dashboard import server
    from fastapi.testclient import TestClient
    import threading
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','chat-test')
    monkeypatch.setattr(server,'_chat_lock',None)
    tid=conversations.create()
    conversations.add_message(tid,'user','The motor is 24 mm.')
    conversations.add_message(tid,'agent','Next we will verify clearance.')
    memory=Memory(); lock=threading.Lock(); seen=[]
    def channel(cid):
        assert cid==f'dashboard:{tid}'
        return memory,lock
    def run(text,**kwargs):
        seen.append(str(memory.messages))
        assert '24 mm' in seen[-1] and 'clearance' in seen[-1]
        return 'Recovered context.'
    monkeypatch.setattr(server,'_agent_ref',NS(_get_channel=channel,run=run))
    c=TestClient(server.app,headers={'Authorization':'Bearer chat-test'})
    response=c.post('/api/chat',json=dict(message='Continue',thread_id=tid,chat_id='new-browser-id'))
    assert response.status_code==200
    assert len(conversations.messages(tid))==4
