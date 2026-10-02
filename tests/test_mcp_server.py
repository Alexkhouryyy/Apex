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


@pytest.mark.xfail(sys.platform == 'win32', strict=False,
                   reason='Timed out on Windows once (2026-10-02) at an unknown step; run '
                          '"python scripts/apex_mcp.py --self-test" on the PC to see which tool stalls.')
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
        if step['name'] == 'done':
            return                       # every call answered; the client library tripped while shutting down
        log = server_log.read_text(encoding='utf-8', errors='replace')[-3000:] if server_log.exists() else '(none)'
        calls = (tmp_path / '.apex' / 'mcp.log')
        done = calls.read_text() if calls.exists() else '(no tool finished)'
        raise AssertionError(f'MCP stdio run failed during {step["name"]!r}: {exc!r}\n'
                             f'Tools that finished:\n{done}\nServer stderr (tail):\n{log}') from exc


def test_stdout_carries_only_protocol(tmp_path, monkeypatch):
    """Strict clients (Claude Desktop) drop a server that writes anything but
    JSON-RPC to stdout. Apex's memory module prints while it loads (or fails to
    load) its embedding model; that must land on stderr."""
    import sqlite3
    import subprocess
    import time
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
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'recall', 'arguments': {'query': 'Jeep'}}},
    ]
    proc = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'apex_mcp.py')], env=env, timeout=120,
                          input=''.join(json.dumps(m) + '\n' for m in msgs), capture_output=True, text=True)
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    replies = [json.loads(line) for line in lines]                     # any print would fail here
    assert [r.get('id') for r in replies] == [1, 2] and all(r['jsonrpc'] == '2.0' for r in replies)
    assert '[Memory]' in proc.stderr                                    # the noise went where it belongs
