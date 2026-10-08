"""Portable regressions for Code permissions and operation races.

Temporary databases and fake agents only; no paid plans or POSIX fake CLIs.
"""
import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import config
from agent import approvals, code_brain, code_engines, code_studio, companion, core, longterm, subscription, work
from dashboard import work as routes
from tests.test_companion import agent


@pytest.fixture
def linked(test_db, tmp_path):
    work.init_db()
    code_studio.init_db()
    with longterm._conn() as db:
        pid = db.execute('INSERT INTO code_projects (name,path,created) VALUES (?,?,?)',
                         ('test', str(tmp_path), time.time())).lastrowid
    p = work.add_project('test', 'software')
    work.update_project(p['id'], code_project_id=pid)
    return work.get_project(p['id']), work.add_task(title='Owner approved', project_id=p['id'], apex_ok=True)


@pytest.mark.parametrize('changes', [
    {'notes': 'Unapproved instructions'}, {'notes': 'Replace and disable', 'apex_ok': False},
    {'project_id': None}, {'area': 'other'}, {'status': 'doing'}, {'priority': 1},
])
def test_shared_task_boundary_checks_before_and_after(linked, changes):
    _, task = linked
    with pytest.raises(work.OwnerRequired):
        work.update_task(task['id'], by_owner=False, **changes)
    assert work.get_task(task['id']) == task
    assert work.update_task(task['id'], by_owner=True, **changes)


def test_quick_add_and_tool_cannot_bypass_owner(linked):
    p, task = linked
    assert 'Only the owner' in work.tool({'action': 'add', 'quick': 'New instructions @test +apex', 'by_owner': True})
    assert 'Only the owner' in work.tool({'action': 'update', 'id': task['id'], 'notes': 'Unapproved', 'by_owner': True})
    assert len(work.list_tasks(include_done=True)) == 1
    assert work.get_task(task['id'])['notes'] == ''
    ordinary = work.add_task(title='Normal task', project_id=p['id'], by_owner=False)
    assert 'Updated' in work.tool({'action': 'update', 'id': ordinary['id'], 'notes': 'Device note'})
    assert 'Added' in work.tool({'action': 'add', 'quick': 'Approved @test +apex'}, by_owner=True)


@pytest.mark.parametrize('body', [{'notes': 'Unapproved project instructions'}, {'client': 'Replace'},
                                  {'name': 'Replace'}, {'code_project_id': None}])
def test_device_project_api_cannot_rewrite_coding_context(linked, monkeypatch, body):
    p, _ = linked
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'synthetic')
    request = Request({'type': 'http', 'method': 'PATCH', 'path': '/', 'headers': []})
    async def payload(*args, **kwargs): return body
    monkeypatch.setattr(routes, '_json', payload)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.edit_project(p['id'], request))
    assert exc.value.status_code == 403
    assert work.get_project(p['id']) == p
    request.state.is_master = True
    assert asyncio.run(routes.edit_project(p['id'], request))


@pytest.mark.parametrize('owner', [False, True])
def test_agent_turn_carries_identity_to_work_tool(agent, linked, monkeypatch, owner):
    _, task = linked
    calls = iter([
        SimpleNamespace(content=[SimpleNamespace(type='tool_use', name='work', id='w1',
            input={'action': 'update', 'id': task['id'], 'notes': 'Owner instructions', 'by_owner': True})],
            stop_reason='tool_use'),
        SimpleNamespace(content=[SimpleNamespace(type='text', text='Done')], stop_reason='end_turn'),
    ])
    monkeypatch.setattr(agent, '_all_tools', lambda: [next(t for t in core.TOOLS if t['name'] == 'work')])
    monkeypatch.setattr(core.telemetry, 'create', lambda *args, **kwargs: next(calls))
    agent.run('Change the notes', companion_mode='work', channel_id='companion:boundary',
              withhold=companion.CODE_TOOLS, by_owner=owner)
    assert work.get_task(task['id'])['notes'] == ('Owner instructions' if owner else '')
    assert core._TURN_OWNER.get() is False


def test_review_has_no_shell_and_no_inherited_write_permissions(tmp_path):
    for engine in ('claude', 'chatgpt'):
        cmd = code_engines.command(engine, engine, tmp_path, 'review',
                                   options={'always': ['npm install'], 'allow': ['python evil.py']})
        assert not any('npm install' in x or 'evil.py' in x for x in cmd)
        if engine == 'claude':
            assert cmd[cmd.index('--tools') + 1] == 'Read,Glob,Grep'
            assert 'Bash' in cmd[cmd.index('--disallowedTools') + 1:]
            assert cmd[cmd.index('--permission-mode') + 1] == 'plan'
        else:
            assert 'sandbox_mode=read-only' in cmd
    ordinary = code_engines.command('claude', 'claude', tmp_path, 'safe', options={'always': ['npm install']})
    assert 'Bash(npm install:*)' in ordinary


def test_review_memory_server_refuses_writes_without_changing_coding_config(test_db, monkeypatch, tmp_path):
    monkeypatch.setattr(work, 'WORK_DIR', tmp_path)
    normal = code_brain.mcp_file(1, 2)
    review = code_brain.mcp_file(1, 2, read_only=True)
    assert normal != review
    assert 'APEX_CODE_READ_ONLY' not in json.loads(normal.read_text())['mcpServers']['apex']['env']
    assert json.loads(review.read_text())['mcpServers']['apex']['env']['APEX_CODE_READ_ONLY'] == '1'
    code_brain.forget_file(1)
    assert not review.exists() and not normal.exists()


def test_subscription_bridge_stages_memory_across_worker_calls(test_db, monkeypatch):
    # Use the real bridge and dispatcher, replacing only the paid SDK execution.
    sdk = SimpleNamespace(tool=lambda *args: lambda fn: fn)
    monkeypatch.setitem(sys.modules, 'claude_agent_sdk', sdk)
    monkeypatch.setattr(subscription, 'should_use', lambda *args: (True, 'test'))
    monkeypatch.setattr(subscription, 'transcript_prompt', lambda *args: 'test')
    monkeypatch.setattr(core.safety, 'check', lambda *args: (True, 'test'))
    monkeypatch.setattr(core, '_code_status', lambda *args: 'synthetic code session')
    a = SimpleNamespace(_model='claude-test', _effective_system_prompt=lambda: 'test',
                        _all_tools=lambda: [next(t for t in core.TOOLS if t['name'] == n)
                                           for n in ('code_status', 'remember')])
    memory = SimpleNamespace(get_messages=lambda: [], add_assistant=lambda data: None)
    def run_turn(system, prompt, tools, dispatch, **kwargs):
        wrapped = subscription._bridge(tools, dispatch)
        async def calls():
            await wrapped[0]({})
            await wrapped[1]({'content': 'Preference taken from the coding agent'})
        asyncio.run(calls())
        return {'text': 'Done', 'is_error': False}
    monkeypatch.setattr(subscription, 'run_turn', run_turn)
    with core._staging_memories(None):
        assert core.AgentCore._try_subscription(a, 'How is code?', memory) == 'Done'
        assert core._STAGE_REMEMBER.get()['who'] == 'Apex, after code_status'
    assert core._STAGE_REMEMBER.get() is None
    assert not longterm.recall('Preference taken', limit=10)
    [pending] = approvals.list_pending()
    assert pending['kind'] == 'remember'


@pytest.mark.parametrize('entry', ['send', 'terminal', 'review', 'run_checks', 'keep', 'undo', 'catch_up', '_discard_idle'])
def test_each_operation_refuses_running_terminal(monkeypatch, entry):
    sid = 999901
    code_studio._terms[sid] = 'fake-command'
    monkeypatch.setattr(code_studio, 'session', lambda *args: pytest.fail('Must reject before reading the session'))
    args = {'send': ('Edit',), 'terminal': ('echo test',), '_discard_idle': ('',)}.get(entry, ())
    try:
        with pytest.raises(code_studio.CodeError, match='busy'):
            getattr(code_studio, entry)(sid, *args)
    finally:
        code_studio._terms.pop(sid, None)


@pytest.mark.parametrize('entry,boundary', [('keep', 'proof'), ('undo', 'events'), ('catch_up', '_on_branch')])
def test_full_synchronous_operation_reserves_session_and_releases_on_error(monkeypatch, tmp_path, entry, boundary):
    sid = 999902
    s = {'id': sid, 'status': 'ready', 'worktree': str(tmp_path), 'project_path': str(tmp_path)}
    monkeypatch.setattr(code_studio, 'session', lambda *args: s)
    entered, release = threading.Event(), threading.Event()
    class EndProbe(code_studio.CodeError): pass
    def at_boundary(*args):
        entered.set()
        if not release.wait(5): raise AssertionError('Test did not release operation')
        raise EndProbe()
    monkeypatch.setattr(code_studio, boundary, at_boundary)
    errors = []
    def run():
        try: getattr(code_studio, entry)(sid)
        except Exception as exc: errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        for op, args in [('terminal', ('echo test',)), ('send', ('Edit',)), ('run_checks', ()), ('_discard_idle', ('',))]:
            with pytest.raises(code_studio.CodeError, match='busy'):
                getattr(code_studio, op)(sid, *args)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], EndProbe)
    assert sid not in code_studio._operations
