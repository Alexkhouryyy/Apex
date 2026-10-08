"""Apex as an MCP server (agent/mcp_server.py, scripts/apex_mcp.py).

Outside AI tools get Apex's context, memory, lessons and skills; what they
get is redacted; and the one write they have, `remember`, only stages a
memory for the owner's approval. The last test runs the real stdio server
under a real MCP client, because a single stray print on stdout breaks the
protocol and Apex's modules print.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from agent import approvals, goals, lessons, longterm, mcp_server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def apex(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, 'LOG', tmp_path / 'mcp.log')
    monkeypatch.setattr(longterm, '_embed', lambda text: None)        # no model download in tests
    monkeypatch.setattr(longterm, '_get_embed_model', lambda: None)    # exercises the text-match fallback
    files = {'user': 'Alex. Prefers short, honest answers rated out of 10.', 'memory': 'Apex runs on an RTX 4070 laptop.'}
    monkeypatch.setattr(longterm, 'load_memory_files', lambda: dict(files))
    goals.init_db()
    approvals.init_db()
    longterm.remember('Alex drives a 2011 Jeep Grand Cherokee.', kind='fact', importance=7)
    longterm.remember('The relay token is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX', kind='note')
    return tmp_path


def test_context_brings_profile_memory_and_goals(apex):
    goals.set_goal('Ship the Voices feature', horizon='week')
    text = mcp_server.context()
    assert 'Prefers short, honest answers' in text and 'RTX 4070' in text
    assert 'Grand Cherokee' in text and 'Ship the Voices feature' in text


def test_secrets_are_redacted_on_the_way_out(apex):
    for text in (mcp_server.context(), mcp_server.recall('relay token')):
        assert 'ABCDEFGHIJKLMNOP' not in text and 'redacted' in text


def test_recall_finds_memories(apex):
    assert 'Grand Cherokee' in mcp_server.recall('Jeep')
    assert 'Nothing in memory' in mcp_server.recall('zebra migration patterns')


def test_lessons_and_skills(apex, monkeypatch):
    monkeypatch.setattr(lessons, 'active', lambda limit=6: [
        {'key': 'k', 'text': 'web_browse on PDFs fails; download then read_file', 'n': 10, 'rate': 0.6}])
    assert 'web_browse on PDFs fails; download then read_file (6/10 recent calls)' in mcp_server.lessons()
    assert isinstance(mcp_server.skills(), str)


def test_remember_only_stages_for_approval(apex):
    before = len(longterm.recall(limit=50))
    reply = mcp_server.remember('Alex wants voice replies kept under 20 seconds.', kind='preference', why='said it twice')
    assert 'STAGED' in reply and 'approval' in reply
    assert len(longterm.recall(limit=50)) == before                     # nothing saved yet
    pending = approvals.list_pending()
    assert pending[0]['kind'] == 'remember' and 'MCP' in pending[0]['summary']
    approvals.approve(pending[0]['id'])
    assert any('under 20 seconds' in m['content'] for m in longterm.recall(limit=50))
    assert mcp_server.remember('hi').startswith('A memory must be')


def test_every_call_is_logged(apex):
    mcp_server.recall('Jeep')
    entry = json.loads((apex / 'mcp.log').read_text().splitlines()[-1])
    assert entry['tool'] == 'recall' and entry['query'] == 'Jeep' and entry['chars'] > 0


# ---------------------------------------------------------------- the project it describes, and Apex Code sessions

HANDOFF = dict(brief='', decisions='', artifacts='', next_step='')


def _code_project(name='Shop'):
    """A code project row, as Apex Code (agent/code_studio.py) keeps them."""
    import time
    from agent import code_studio
    code_studio.init_db()
    with longterm._conn() as db:
        return db.execute('INSERT INTO code_projects (name, path, created) VALUES (?, ?, ?)',
                          (name, f'/tmp/{name}', time.time())).lastrowid


def test_context_carries_the_board_handoff(apex):
    """continuity.project() keeps the handoff under 'data': context() used to read the
    top level and only ever said 'Project: <name>'."""
    from agent import board_workspaces, continuity
    board_workspaces.ensure_db()
    continuity.save_project('default', dict(HANDOFF, decisions='Use SQLite', next_step='Ship the sync'), 0)
    continuity.save_corrections('default', [{'text': 'No ORMs', 'active': True}, {'text': 'Old one', 'active': False}], 0)
    text = mcp_server.context()
    assert '## Current project\nProject: My workspace' in text
    assert 'Decisions: Use SQLite' in text and 'Next step: Ship the sync' in text
    assert 'Corrections the user asked for: No ORMs' in text and 'Old one' not in text


def test_in_an_apex_code_session_context_is_that_code_project(apex, monkeypatch):
    from agent import board_workspaces, code_brain, continuity
    board_workspaces.ensure_db()
    continuity.save_project('default', dict(HANDOFF, decisions='Use SQLite'), 0)
    pid = _code_project('Shop')
    continuity.save_code_project(pid, dict(HANDOFF, decisions='Cache by user id', next_step='Add the checkout'), 0)
    continuity.save_code_corrections(pid, [{'text': 'Never touch the payments module', 'active': True},
                                           {'text': 'An old rule', 'active': False}], 0)
    code_brain.add_rule(pid, 'Always write type hints', 'all')
    monkeypatch.setenv('APEX_CODE_PROJECT', str(pid))
    text = mcp_server.context()
    assert '## This code project (Apex Code)\nProject: Shop' in text
    assert 'Decisions: Cache by user id' in text and 'Next step: Add the checkout' in text
    assert 'Rules the user gave for this project: Never touch the payments module' in text
    assert 'Rules the user gave for all their code: Always write type hints' in text
    assert 'Use SQLite' not in text and 'An old rule' not in text            # not the board's, not a rule turned off


def test_recall_matches_the_words_of_a_question(apex):
    """An engine asks in a few words; the text search wanted all of them in one memory."""
    longterm.remember('Uploads must retry 3 times', kind='decision')
    assert longterm.recall('upload retry policy', semantic=False) == []
    assert 'Uploads must retry 3 times' in mcp_server.recall('upload retry policy')
    assert 'Nothing in memory' in mcp_server.recall('zebra migration patterns')


def test_a_memory_an_apex_code_session_suggests_waits_for_the_owner_then_reaches_the_brief(apex, monkeypatch):
    from agent import code_brain
    pid = _code_project('Shop')
    monkeypatch.setenv('APEX_CODE_PROJECT', str(pid))
    monkeypatch.setenv('APEX_CODE_SESSION', '7')
    reply = mcp_server.remember('Alex wants errors logged with structlog', kind='preference', why='said in the request')
    assert 'STAGED' in reply and 'Waiting for your OK' in reply
    pending = approvals.list_pending()[0]
    assert pending['summary'].startswith(f'Apex Code session, project {pid} suggests remembering [preference]')
    assert pending['payload']['source'] == f'Apex Code session, project {pid}'
    assert (pending['payload']['project_id'], pending['payload']['session_id']) == (pid, 7)
    assert not any('structlog' in m['content'] for m in longterm.recall(limit=50))    # nothing saved yet
    # What the Code page lists: Apex Code's suggestions only, never another tool's.
    monkeypatch.delenv('APEX_CODE_PROJECT')
    mcp_server.remember('Alex likes the Futuristic look.', kind='preference')
    waiting = code_brain.suggested()
    assert [(w['content'], w['project_id'], w['session_id']) for w in waiting] == [('Alex wants errors logged with structlog', pid, 7)]
    # Approved, it is a coding preference: the next session's brief brings it back.
    approvals.approve(waiting[0]['id'])
    saved = next(m for m in longterm.recall(limit=50) if 'structlog' in m['content'])
    assert saved['kind'] == 'preference' and saved['tags'] == 'from-mcp,code'
    assert {'kind': 'memory', 'ref': saved['id'], 'text': 'Alex wants errors logged with structlog'} in code_brain.brief_block(pid)['sources']
    assert code_brain.suggested() == []


def test_the_second_opinion_cannot_suggest_a_memory(apex, monkeypatch):
    monkeypatch.setenv('APEX_CODE_PROJECT', '3')
    monkeypatch.setenv('APEX_CODE_READ_ONLY', '1')
    assert 'read-only' in mcp_server.remember('Alex wants errors logged with structlog', kind='preference')
    assert approvals.list_pending() == []


def test_real_stdio_server_under_a_real_client(tmp_path):
    """A real MCP client drives the real server: every tool answers, step by
    step, and a stall names its step and shows the server's own log."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ, DB_PATH=str(tmp_path / 'apex.db'), VAULT_PATH=str(tmp_path / 'vault'),
               HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               ANTHROPIC_API_KEY='sk-ant-placeholder-for-ci-tests-only', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'scripts' / 'apex_mcp.py')], env=env)
    server_log = tmp_path / 'server-stderr.txt'
    step = {'name': 'starting the server'}

    async def run():
        with server_log.open('w', encoding='utf-8') as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    async def do(name, call):
                        step['name'] = name
                        return await asyncio.wait_for(call, 45)
                    await do('initialize', session.initialize())
                    names = {t.name for t in (await do('list_tools', session.list_tools())).tools}
                    assert names == {'context', 'recall', 'lessons', 'skills', 'skill', 'search_files', 'remember'}
                    staged = await do('remember', session.call_tool('remember', {'content': 'Alex likes the Futuristic look.', 'kind': 'preference'}))
                    assert 'STAGED' in staged.content[0].text
                    recalled = await do('recall', session.call_tool('recall', {'query': 'look'}))
                    assert not recalled.isError
                    ctx = await do('context', session.call_tool('context', {}))
                    assert not ctx.isError and ctx.content[0].text
                    step['name'] = 'done'

    try:
        asyncio.run(run())
    except BaseException as exc:
        log = server_log.read_text(encoding='utf-8', errors='replace')[-3000:] if server_log.exists() else '(none)'
        calls = (tmp_path / '.apex' / 'mcp.log')
        done = calls.read_text() if calls.exists() else '(no tool finished)'
        raise AssertionError(f'MCP stdio run failed during {step["name"]!r}: {exc!r}\n'
                             f'Tools that finished:\n{done}\nServer stderr (tail):\n{log}') from exc


def test_stdout_carries_only_protocol(tmp_path, monkeypatch):
    """Strict clients (Claude Desktop) drop a server that writes anything but
    JSON-RPC to stdout. Exercise Python and native stdout noise from the memory
    callback without downloading an embedding model; both must reach stderr."""
    import sqlite3
    import subprocess
    import time
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(longterm, 'DB_PATH', str(tmp_path / 'apex.db'))
    longterm.init_db()
    with sqlite3.connect(tmp_path / 'apex.db') as db:                  # a memory, so recall loads the embedder
        db.execute("INSERT INTO memories (ts, kind, content, importance, tags) VALUES (?, 'fact', 'Alex drives a Jeep.', 5, '')",
                   (time.time(),))
    env = dict(os.environ, DB_PATH=str(tmp_path / 'apex.db'), VAULT_PATH=str(tmp_path / 'vault'),
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               ANTHROPIC_API_KEY='sk-ant-placeholder-for-ci-tests-only')
    msgs = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'recall', 'arguments': {'query': 'Jeep', 'semantic': True}}},
    ]
    runner = tmp_path / 'noisy_server.py'
    runner.write_text('import sys, os\n' + f'sys.path.insert(0, {str(ROOT)!r})\n'
        'from agent import longterm\n'
        'def noisy_model():\n'
        '    print("[Memory] optional model unavailable")\n'
        '    os.write(1, b"[Native] stdout noise\\n")\n'
        '    return None\n'
        'longterm._get_embed_model = noisy_model\n'
        'from scripts.apex_mcp import main\nmain()\n', encoding='utf-8')
    # A client keeps stdin open until replies arrive. Sending EOF with the
    # request allows the SDK to cancel a worker before it returns its reply.
    log = tmp_path / 'protocol-stderr.txt'
    replies = []
    with log.open('w', encoding='utf-8') as errlog, ThreadPoolExecutor(max_workers=1) as pool:
        proc = subprocess.Popen([sys.executable, str(runner)], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errlog, text=True, encoding='utf-8')
        try:
            proc.stdin.write(json.dumps(msgs[0]) + '\n')
            proc.stdin.flush()
            replies.append(json.loads(pool.submit(proc.stdout.readline).result(timeout=30)))
            proc.stdin.write(''.join(json.dumps(m) + '\n' for m in msgs[1:]))
            proc.stdin.flush()
            replies.append(json.loads(pool.submit(proc.stdout.readline).result(timeout=90)))
            proc.stdin.close()
            proc.wait(timeout=10)
            assert not proc.stdout.read().strip(), 'Unexpected extra protocol output'
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            proc.stdout.close()
            if not proc.stdin.closed:
                proc.stdin.close()
    assert proc.returncode == 0
    assert [r.get('id') for r in replies] == [1, 2] and all(r['jsonrpc'] == '2.0' for r in replies)
    assert 'Jeep' in str(replies[1])
    noise = log.read_text(encoding='utf-8')
    assert '[Memory]' in noise and '[Native]' in noise
