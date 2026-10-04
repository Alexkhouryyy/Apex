"""Complete local workflow, durable restart, isolation and honest storage failures."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace as NS

import httpx
from openai import OpenAI
import pytest
from fastapi.testclient import TestClient

import config
from agent import board, board_workspaces, continuity, conversations, core, offline_sessions, provider
from dashboard import apocalypse as api, server

OWNER = {'Authorization': 'Bearer workflow-owner', 'Origin': 'http://testserver'}


@pytest.fixture
def isolated(test_db, monkeypatch, tmp_path):
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    monkeypatch.setenv('APEX_APOCALYPSE_HOME', str(tmp_path))
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'workflow-owner')
    monkeypatch.setattr(config, 'SMART_ROUTING_ENABLED', False)
    monkeypatch.setattr(board, '_board', None)
    monkeypatch.setattr(board_workspaces, '_boards', {})
    monkeypatch.setattr(core.longterm, 'load_memory_files', lambda: {})
    monkeypatch.setattr(core._budget, 'check', lambda: None)
    monkeypatch.setattr(api, '_chat_lock', asyncio.Lock())
    board.get_board()
    from agent import goals
    goals.init_db()
    server._throttle.reset('testclient')
    return tmp_path


def model(monkeypatch, handler):
    from agent import apocalypse
    monkeypatch.setattr(apocalypse, 'verify_model', lambda *a: None)
    sdk = OpenAI(api_key='ollama', base_url='http://127.0.0.1:11435/v1', max_retries=0,
                 http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    agent = core.AgentCore()
    agent._model = 'ollama/qwen3:4b'
    agent._provider_clients['ollama'] = NS(messages=provider._Messages(sdk, strip_prefix='ollama/'))
    monkeypatch.setattr(agent, '_rerank_eligible', lambda *a: False)
    monkeypatch.setattr(server, '_agent_ref', agent)
    return agent


def reply(text='', tool=None):
    return httpx.Response(200, json={'id': 'offline', 'choices': [{'finish_reason': 'tool_calls' if tool else 'stop',
        'message': {'role': 'assistant', 'content': text, 'tool_calls': [tool] if tool else None}}]})


def call(name, args):
    return {'id': 'call-'+name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def test_document_report_checkpoint_survives_fresh_process_without_tool_replay(isolated, monkeypatch, test_db):
    source = isolated/'documents'/'fixture.md'
    source.parent.mkdir()
    source.write_text('Project Cedar: sensor requires 3.3 V. Never connect it to 5 V.', encoding='utf-8')
    report = isolated/'cedar-report.md'
    handoff = dict(brief='Cedar sensor report', decisions='Use 3.3 V.', artifacts=str(report), next_step='Review the wiring diagram.')
    steps = [call('read_file', {'path': str(source)}),
             call('write_file', {'path': str(report), 'content': '# Cedar\nSupply: 3.3 V.\nSource: fixture.md\n'}),
             call('read_file', {'path': str(report)}),
             call('project_checkpoint', {'action': 'save', 'revision': 0, 'data': handoff})]
    sent = []
    def handler(request):
        data=json.loads(request.content);sent.append(data)
        if len(sent)>1:
            assert data['messages'][-1]['role']=='tool'
            assert '[BLOCKED' not in data['messages'][-1]['content']
        if len(sent)==4:
            assert 'Supply: 3.3 V' in data['messages'][-1]['content']
        if len(sent)==5:
            assert json.loads(data['messages'][-1]['content'])['revision']==1
        return reply(tool=steps[len(sent)-1]) if len(sent)<=4 else reply('Report saved and read back. Next: review the wiring diagram.')
    agent=model(monkeypatch, handler)
    # Exact fixture output only; never bypass runtime approval for owner files.
    from agent import safety
    monkeypatch.setattr(safety, 'check', lambda name, inputs: (True, 'fixture') if name != 'write_file' or inputs['path']==str(report) else (False, 'outside fixture'))
    client=TestClient(server.app)
    result=client.post('/api/apocalypse/chat', json={'message':'Create and verify the Cedar report from the local source, then save the project handoff.', 'workspace_id':'default'}, headers=OWNER)
    assert result.status_code==200, result.text
    assert result.json()['saved'] is True and len(sent)==5
    tid=result.json()['thread_id']
    assert report.read_text().startswith('# Cedar')
    assert len(conversations.messages(tid))==2
    assert continuity.project('default')['data']==handoff
    # A real new interpreter opens only this isolated database. No provider call
    # or old tool invocation is needed to recover the checked artifact/history.
    script='''import json, pathlib
from agent import offline_sessions, continuity
from agent.memory import Memory
s=offline_sessions.session('default')
m=Memory()
with continuity.turn('apocalypse:'+str(s['thread_id'])):
 with continuity.conversation('apocalypse:'+str(s['thread_id']),object(),m,'Resume the project'):
  assert 'Cedar report' in str(m.messages)
  assert 'wiring diagram' in continuity.prompt()
  p=continuity.checkpoint(action='read')
  assert 'Supply: 3.3 V' in pathlib.Path(p['data']['artifacts']).read_text()
  assert all(b['type']=='text' for item in m.messages for b in item['content'])
  m.add_user('Resume the project')
  m.add_assistant([dict(type='text',text='Resume at the wiring diagram.')])
print(json.dumps(dict(recovered=True,thread_id=s['thread_id'])))
'''
    child=subprocess.run([sys.executable,'-X','utf8','-c',script],cwd=Path(__file__).resolve().parents[1],
        env={**os.environ,'DB_PATH':test_db},capture_output=True,text=True,timeout=30)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)['thread_id']==tid
    assert len(conversations.messages(tid))==4
    restored=client.get('/api/apocalypse/session?workspace_id=default',headers=OWNER).json()
    assert restored['messages'][-1]['text']=='Resume at the wiring diagram.'
    assert agent._get_channel('apocalypse:'+str(tid))==agent._get_channel('companion:'+str(tid))


def test_projects_remain_separate_and_selection_does_not_move_the_board(isolated):
    second=board_workspaces.create('Second',False,board.get_board().workspace_context())
    first=offline_sessions.session('default',create=True)
    other=offline_sessions.session(second['id'],create=True)
    conversations.add_message(first['thread_id'],'user','FIRST PROJECT SECRET',strict=True)
    conversations.add_message(other['thread_id'],'user','SECOND PROJECT',strict=True)
    board_workspaces.switch(second['id'],board.get_board().workspace_context())
    with continuity.turn('apocalypse:'+str(first['thread_id'])):
        assert continuity._turn.get()['project']['id']=='default'
        continuity.checkpoint(dict(brief='First',decisions='',artifacts='',next_step='First next'),0)
    assert offline_sessions.session('default')['messages'][0]['text']=='FIRST PROJECT SECRET'
    assert 'FIRST PROJECT SECRET' not in str(offline_sessions.session(second['id']))
    assert offline_sessions.session()['project']['id']==second['id']
    assert continuity.project(second['id'])['revision']==0
    with continuity.turn('sms:'+str(first['thread_id'])):
        assert continuity.prompt()=='' and continuity.persona_block() is None


def test_owner_origin_and_stale_handoff_protection(isolated, monkeypatch):
    client=TestClient(server.app)
    assert client.get('/api/apocalypse/session').status_code==401
    monkeypatch.setattr('agent.access_tokens.verify',lambda token:token=='peer')
    assert client.get('/api/apocalypse/session',headers={'Authorization':'Bearer peer'}).status_code==403
    assert client.get('/api/apocalypse/session?workspace_id=missing',headers=OWNER).status_code==400
    data=dict(data=dict(brief='A draft',decisions='',artifacts='',next_step=''),revision=0)
    assert client.post('/api/apocalypse/projects/default',json=data,headers={**OWNER,'Origin':'https://foreign.test'}).status_code==403
    assert client.post('/api/apocalypse/projects/default',json=data,headers=OWNER).status_code==200
    assert client.post('/api/apocalypse/projects/default',json=data,headers=OWNER).status_code==409
    assert len(continuity.project_history('default'))==1


def test_storage_failure_prevents_execution_and_never_claims_saved(isolated, monkeypatch):
    invoked=[]
    agent=model(monkeypatch,lambda r: invoked.append(r) or reply('Unsafe success'))
    def fail(*a,**kw): raise sqlite3.OperationalError('private path')
    monkeypatch.setattr(conversations,'add_message',fail)
    client=TestClient(server.app)
    result=client.post('/api/apocalypse/chat',json={'message':'Work'},headers=OWNER)
    assert result.status_code==503 and 'private path' not in result.text
    assert not invoked and 'saved' not in result.json()


def test_failed_turn_retains_request_and_safe_marker_without_replay(isolated, monkeypatch):
    calls=[]
    def handler(r):
        calls.append(json.loads(r.content))
        return reply() if len(calls)==1 else reply('Recovered final reply')
    model(monkeypatch,handler)
    client=TestClient(server.app)
    assert client.post('/api/apocalypse/chat',json={'message':'Hard question'},headers=OWNER).status_code==503
    history=offline_sessions.session('default')['messages']
    assert history[0]['text']=='Hard question' and 'without a final reply' in history[1]['text']
    assert client.post('/api/apocalypse/chat',json={'message':'One small part'},headers=OWNER).status_code==200
    assert len(calls)==2


def test_deleted_thread_is_recreated_without_resurrecting_history(isolated):
    old=offline_sessions.session('default',create=True)
    conversations.add_message(old['thread_id'],'user','Deleted text',strict=True)
    assert conversations.delete(old['thread_id'])
    new=offline_sessions.session('default',create=True)
    assert new['thread_id']!=old['thread_id'] and new['messages']==[]


def test_offline_prompt_receives_saved_project_and_checkpoint_tool(isolated, monkeypatch):
    s=offline_sessions.session('default',create=True)
    continuity.save_project('default',dict(brief='Study fixture',decisions='',artifacts='',next_step='Measure current'),0)
    agent=core.AgentCore()
    with continuity.turn('apocalypse:'+str(s['thread_id'])):
        assert 'Measure current' in str(agent._effective_system_prompt())
        names={tool['name'] for tool in agent._all_tools()}
        assert 'project_checkpoint' in names
        assert 'web_search' not in names


def test_prose_checkpoint_claim_is_corrected_and_saved_in_history(isolated,monkeypatch):
    model(monkeypatch,lambda r:reply('Project checkpoint saved.'))
    client=TestClient(server.app)
    result=client.post('/api/apocalypse/chat',json={'message':'Save the next step'},headers=OWNER)
    assert result.status_code==200
    assert 'No project handoff was saved by this turn' in result.json()['answer']
    assert continuity.project('default')['revision']==0
    assert 'No project handoff was saved by this turn' in offline_sessions.session('default')['messages'][-1]['text']


def test_successful_checkpoint_has_no_false_warning_and_does_not_leak_to_next_turn(isolated):
    s=offline_sessions.session('default',create=True)
    with continuity.turn('apocalypse:'+str(s['thread_id'])):
        continuity.checkpoint(dict(brief='Actual save',decisions='',artifacts='',next_step='Review'),0)
        assert continuity.checked_reply('Project handoff saved.')=='Project handoff saved.'
    with continuity.turn('apocalypse:'+str(s['thread_id'])):
        assert 'No project handoff was saved by this turn' in continuity.checked_reply('Project handoff saved.')
