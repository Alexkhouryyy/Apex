import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from agent import apocalypse, provider
from dashboard import apocalypse as api, server
from scripts import setup_apex_apocalypse as setup

ROOT = Path(__file__).resolve().parents[1]
OWNER = {'Authorization':'Bearer apocalypse-owner','Origin':'http://testserver'}


@pytest.fixture
def client(monkeypatch,tmp_path):
    import config
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','apocalypse-owner')
    monkeypatch.setenv('APEX_APOCALYPSE_HOME',str(tmp_path))
    monkeypatch.setattr(api,'_chat_lock',asyncio.Lock())
    server._throttle.reset('testclient')
    return TestClient(server.app)


def test_offline_guard_blocks_dns_and_direct_egress_but_allows_local_socket():
    code='''import os,socket
os.environ['APEX_APOCALYPSE']='1'
from agent.apocalypse import install_network_guard,OfflineUnavailable
install_network_guard()
for fn in (lambda:socket.getaddrinfo('example.com',443),lambda:socket.socket().connect(('203.0.113.1',443))):
 try: fn()
 except OfflineUnavailable: pass
 else: raise AssertionError('Internet operation escaped the boundary')
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
c=socket.socket();c.connect(s.getsockname());p,_=s.accept()
c.close();p.close();s.close()
print('local-only boundary passed')
'''
    r=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env={**os.environ,'APEX_APOCALYPSE':'1'},
                     capture_output=True,text=True,timeout=15)
    assert r.returncode==0,r.stderr


@pytest.mark.parametrize('url',['https://example.com','http://192.168.1.2:11434','http://localhost.evil:11434',
                                'http://user:secret@localhost:11434','http://127.0.0.1/?secret=x'])
def test_remote_and_credential_urls_refused(url):
    with pytest.raises(apocalypse.OfflineUnavailable):apocalypse.loopback_url(url)


def test_child_environment_keeps_owner_auth_and_does_not_change_normal_settings(tmp_path):
    (tmp_path/'.env').write_text('AGENT_MODEL=claude-opus-5\nANTHROPIC_API_KEY=private-key\nDASHBOARD_TOKEN=owner\n')
    env=apocalypse.launch_environment(tmp_path,{'HTTP_PROXY':'https://remote.test','APEX_APOCALYPSE_HOME':str(tmp_path)})
    assert env['ANTHROPIC_API_KEY']=='' and env['DASHBOARD_TOKEN']=='owner'
    assert env['AGENT_MODEL']==env['BACKGROUND_MODEL']==env['PROACTIVE_MODEL']=='ollama/qwen3:4b'
    assert env['HF_HUB_OFFLINE']=='1' and 'HTTP_PROXY' not in env
    assert 'private-key' in (tmp_path/'.env').read_text()


def test_cloud_client_and_cached_client_refused(monkeypatch):
    monkeypatch.setenv('APEX_APOCALYPSE','1')
    with pytest.raises(apocalypse.OfflineUnavailable):provider.get_client('gpt-4o')
    from agent.core import AgentCore
    core=object.__new__(AgentCore);core._model='claude-opus-5';core.anthropic=object()
    with pytest.raises(apocalypse.OfflineUnavailable):_ = core.client
    core._model='gpt-4o';core._provider_clients={'openai':object()}
    with pytest.raises(apocalypse.OfflineUnavailable):_ = core.client
    assert 'local Ollama model' in core.set_model('gpt-4o')
    assert core._model=='gpt-4o'
    assert core.load_mcp_tools()==0
    names={t['name'] for t in core._all_tools()}
    assert {'read_file','recall','kb_search'}<=names
    assert not names & {'web_search','bash','telegram_send','generate_image','spawn_subagent'}


def test_cloud_ollama_aliases_and_remote_weights_refused(monkeypatch):
    assert not apocalypse.is_local_model({'name':'qwen3:cloud','size':100})
    assert not apocalypse.is_local_model({'name':'alias','remote_host':'https://ollama.com'})
    assert apocalypse.is_local_model({'name':'qwen3:4b','size':2500000000})
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(
        lambda r:httpx.Response(200,json={'remote_model':'big-cloud-model','model_info':{}})),**kw))
    with pytest.raises(apocalypse.OfflineUnavailable):apocalypse.verify_model('ollama/alias','http://127.0.0.1:11435/v1')


def test_owner_boundary_and_cross_origin_upload(client,monkeypatch):
    assert client.get('/apocalypse').status_code==200
    assert client.get('/api/apocalypse/status').status_code==401
    monkeypatch.setattr('agent.access_tokens.verify',lambda token:token=='peer')
    assert client.get('/api/apocalypse/status',headers={'Authorization':'Bearer peer'}).status_code==403
    assert client.post('/api/apocalypse/documents/note.txt',content=b'hello',headers={**OWNER,'Origin':'https://foreign.test'}).status_code==403


def test_local_document_no_overwrite_and_safe_download(client,tmp_path):
    path='/api/apocalypse/documents/Local%20note.md'
    assert client.post(path,content=b'# Local knowledge',headers=OWNER).status_code==200
    assert client.post(path,content=b'overwrite',headers=OWNER).status_code==409
    response=client.get(path,headers=OWNER)
    assert response.content==b'# Local knowledge'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['cache-control']=='no-store'
    with pytest.raises(HTTPException):api.document_path('../.env')
    assert client.post('/api/apocalypse/documents/unsafe.html',content=b'<script>',headers=OWNER).status_code==400
    assert client.post('/api/apocalypse/documents/empty.txt',content=b'',headers=OWNER).status_code==400
    assert client.post('/api/apocalypse/documents/large.txt',content=b'x'*(8*1024*1024+1),headers=OWNER).status_code==413


def test_normal_session_cannot_call_offline_chat(client,monkeypatch):
    monkeypatch.delenv('APEX_APOCALYPSE',raising=False)
    assert client.post('/api/apocalypse/chat',json={'message':'hi'},headers=OWNER).status_code==409


def test_readiness_does_not_claim_live_verification(client,monkeypatch):
    calls=[];real=httpx.AsyncClient
    def handler(request):
        calls.append(request)
        assert request.url.host in {'127.0.0.1','localhost'}
        assert 'authorization' not in request.headers
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'qwen3:4b'},{'name':'remote:cloud'}]})
        return httpx.Response(200,json={'status':'ok'})
    monkeypatch.setattr(api.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    response=client.get('/api/apocalypse/status',headers=OWNER)
    assert response.status_code==200
    data=response.json()
    assert data['model']['available']==['qwen3:4b']
    assert data['model']['downloaded'] is True and data['model']['response_verified'] is False
    assert data['offline_verified'] is False and data['voice']['audio_verified'] is False
    assert len(calls)==3


def test_pinned_nomad_source_and_generated_compose(tmp_path):
    manifest=json.loads((ROOT/'integrations/project-nomad/UPSTREAM.json').read_text())
    archive=ROOT/'integrations/project-nomad-source.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest['archive_sha256']
    with ZipFile(archive) as z:
        assert z.comment.decode()==manifest['revision']
        assert len([x for x in z.infolist() if not x.is_dir()])==manifest['source_files']
    folder,compose=setup.nomad_files(tmp_path)
    data=json.loads(compose.read_text())
    assert data['services']['admin']['ports']==['127.0.0.1:8080:8080']
    assert 'updater' not in data['services'] and 'dozzle' not in data['services']
    assert data['services']['admin']['build']['args']['VCS_REF']==manifest['revision']
    original=(folder/'.env').read_bytes()
    setup.nomad_files(tmp_path)
    assert (folder/'.env').read_bytes()==original
    source=folder/('source-'+manifest['revision'][:12])
    (source/'Dockerfile').write_text('FROM malicious-local-change')
    with pytest.raises(RuntimeError,match='source changed'):setup.nomad_files(tmp_path)


def test_cached_nomad_start_never_downloads(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(setup.shutil,'which',lambda name:'docker')
    monkeypatch.setattr(setup,'nomad_files',lambda home:(tmp_path,tmp_path/'compose.json'))
    def run(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,0,stdout='linux\n')
    monkeypatch.setattr(setup.subprocess,'run',run)
    setup.nomad(tmp_path,prepare=False)
    assert calls[-1][-5:]==['up','-d','--pull','never','--no-build']
    assert not any('build' in command or 'pull' in command for command in calls)


def test_offline_dispatch_and_plugins_cannot_bypass_tool_filter(monkeypatch):
    from agent import core, plugins, resilience
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    monkeypatch.setattr(plugins, '_state', lambda: pytest.fail('Plugin state should not be consulted'))
    assert plugins._active() == []
    assert plugins.provider_call('memory', 'recall', query='hello') == (False, None)
    assert plugins.emit('post_tool_call', tool_name='read_file') is None
    with pytest.raises(apocalypse.OfflineUnavailable):
        plugins._load({})
    for name in ('bash', 'web_search', 'plugin__custom', 'spawn_subagent'):
        assert 'PAUSED in Apocalypse' in core._execute_tool(name, {})
        assert 'PAUSED in Apocalypse' in core._execute_tool_inner(name, {})
    assert not resilience.should_fallback('network')
    with pytest.raises(apocalypse.OfflineUnavailable):
        resilience.fallback_create([{'role': 'user', 'content': 'hello'}])


def test_cached_adapter_checks_each_model_before_request(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    from unittest.mock import MagicMock
    client = SimpleNamespace(base_url='http://127.0.0.1:11435/v1', chat=MagicMock())
    adapter = provider._Messages(client, strip_prefix='ollama/')
    with pytest.raises(apocalypse.OfflineUnavailable):
        adapter.create(model='ollama/large:cloud', messages=[])
    client.base_url = 'https://api.openai.com/v1'
    with pytest.raises(apocalypse.OfflineUnavailable):
        adapter.create(model='ollama/qwen3:4b', messages=[])


def test_local_prompt_is_compact_and_files_are_paged(monkeypatch, tmp_path):
    from agent import core
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    monkeypatch.setenv('APEX_APOCALYPSE_HOME', str(tmp_path))
    monkeypatch.setattr(core.goals, 'active_goals_for_prompt', lambda: 'local goal')
    brain = object.__new__(core.AgentCore)
    brain._memory_files = {'memory': 'M' * 8000, 'user': 'U' * 8000}
    prompt = brain._effective_system_prompt()
    assert len(prompt) == 1 and len(prompt[0]['text']) < 6500
    assert str(tmp_path / 'documents') in prompt[0]['text']
    note = tmp_path / 'book.md'
    note.write_text('a' * 4000 + 'SECOND', encoding='utf-8')
    first = apocalypse.read_local_file(note)
    assert 'SECOND' not in first and 'more text: True' in first
    assert 'SECOND' in apocalypse.read_local_file(note, offset=4000)
    assert 'paused' in apocalypse.read_local_file(r'\\server\share\file.txt')


def test_offline_conversation_compression_uses_local_client_without_plugins(monkeypatch):
    from types import SimpleNamespace
    from agent import memory, plugins
    monkeypatch.setenv('APEX_APOCALYPSE', '1')
    monkeypatch.setattr(plugins, 'provider_call', lambda *a, **kw: pytest.fail('Custom context engine ran'))
    sentinel = object()
    calls = []
    def create(client, **kwargs):
        assert client is sentinel
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text='{"summary":"Local summary", "save_to_memory":[]}')])
    monkeypatch.setattr(memory.telemetry, 'create', create)
    mem = memory.Memory()
    for index in range(12):
        mem.add_user('turn ' + str(index))
    mem.maybe_summarize(sentinel)
    assert mem.summary == 'Local summary' and len(mem.messages) == 6
    assert calls[0]['call_site'] == 'agent.memory/maybe_summarize'
