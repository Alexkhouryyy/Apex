"""Managed app lifecycle and execution boundaries, without external accounts."""
import json
import errno
from pathlib import Path
import time
import pytest
from agent import apps, app_tools, mcp_policy, mcp_client, subagent_scope
import config
HTTP_REQUEST = apps._request


@pytest.fixture
def provider(tmp_path, monkeypatch, test_db):
    monkeypatch.setattr(apps, 'ROOT', tmp_path)
    monkeypatch.setenv('COMPOSIO_API_KEY', 'test-project-key')
    monkeypatch.setattr(apps, '_tool_cache', {})
    calls = []
    fixture = {'active': False, 'owner': None, 'account': 'ca_one', 'fail_delete': False}
    def request(method, path, *, params=None, body=None, key=None):
        calls.append((method,path,params,body))
        if path == '/toolkits/categories': return {'items':[{'id':'dev','name':'Developer tools'}]}
        if path.startswith('/toolkits'):
            card={'slug':'github','name':'GitHub','meta':{'description':'Code and issues','tools_count':2,'categories':[{'name':'Developer tools'}]}}
            return {'items':[card], 'next_cursor':'next', 'total_items':1001} if path=='/toolkits' else card
        if path == '/tool_router/session':
            fixture['owner']=body['user_id']
            assert body['premium_usage'] is False
            assert body['manage_connections']=={'enable':False}
            assert body['workbench']=={'enable':False}
            return {'session_id':'trs_one'}
        if method=='PATCH': return {}
        if path.endswith('/link'): return {'connected_account_id':'ca_one','redirect_url':'https://app.composio.dev/link/one'}
        if path.endswith('/toolkits'):
            return {'items':[{'slug':'github','connected_account':{'id':fixture['account'],'user_id':fixture['owner'],'status':'ACTIVE' if fixture['active'] else 'INITIATED'}}]}
        if path.endswith('/tools'):
            base={'toolkit':{'slug':'github'},'input_parameters':{'repo':{'type':'string','required':True}}}
            return {'items':[{**base,'slug':'GITHUB_GET_ISSUE','description':'Read issue'}, {**base,'slug':'GITHUB_DELETE_REPOSITORY','description':'Delete repo'}, {**base,'slug':'COMPOSIO_EXECUTE_TOOL','description':'Do not expose'}, {**base,'slug':'SLACK_SEND_MESSAGE','toolkit':{'slug':'slack'}}]}
        if path.endswith('/execute'): return {'data':{'ok':True},'log_id':'log_one'}
        if method=='DELETE':
            if fixture['fail_delete']: raise apps.AppError('Provider unavailable.')
            return {}
        raise AssertionError((method,path))
    monkeypatch.setattr(apps, '_request', request)
    monkeypatch.setattr(config, 'MCP_POLICY', 'read-only')
    monkeypatch.setattr(config, 'MCP_ALLOW', [])
    monkeypatch.setattr(config, 'MCP_DENY', [])
    fixture['calls']=calls
    return fixture


def connected(provider):
    apps.connect('github'); provider['active']=True; apps.refresh()


def _windows_lock(code):
    error = PermissionError(errno.EACCES, 'file temporarily locked')
    error.winerror = code
    return error


@pytest.mark.parametrize('code', [5, 32, 33])
def test_save_retries_windows_lock_without_repeating_oauth(provider, monkeypatch, code):
    replace = Path.replace
    attempts, delays = [], []
    monkeypatch.setattr(apps.time, 'sleep', delays.append)

    def locked(source, target):
        attempts.append(source)
        if len(attempts) in (2, 3):  # Lock the second save, after OAuth link creation.
            assert apps._state()['apps'] == {}  # Previous JSON remains valid.
            raise _windows_lock(code)
        return replace(source, target)

    monkeypatch.setattr(Path, 'replace', locked)
    link = apps.connect('github')
    assert link['redirect_url'].startswith('https://app.composio.dev/')
    assert len([c for c in provider['calls'] if c[1].endswith('/link')]) == 1
    assert delays == [0.05, 0.1]
    assert len(attempts) == 4
    assert attempts[0] != attempts[1]  # Each save owns its temporary file.
    assert attempts[1] == attempts[2] == attempts[3]  # Rename retries reuse it.
    assert apps._state()['apps']['github']['status'] == 'pending'
    assert list(apps._path().parent.glob('*.tmp')) == []


@pytest.mark.parametrize('winerror, expected_attempts', [(5, 6), (32, 6), (33, 6), (None, 1)])
def test_save_failure_preserves_settings_and_cache(provider, monkeypatch, winerror, expected_attempts):
    old = apps._state()
    apps._save(old)
    previous = apps._path().read_bytes()
    apps._tool_cache['sentinel'] = 'unchanged'
    attempts = []
    monkeypatch.setattr(apps.time, 'sleep', lambda _: None)

    def denied(source, target):
        attempts.append(source)
        if winerror is None:
            raise OSError(errno.ENOSPC, 'disk full')
        raise _windows_lock(winerror)

    monkeypatch.setattr(Path, 'replace', denied)
    with pytest.raises(apps.AppError, match='Existing saved settings were preserved'):
        apps._save({**old, 'session_id': 'new-session'})
    assert len(attempts) == expected_attempts
    assert apps._path().read_bytes() == previous
    assert apps._tool_cache == {'sentinel': 'unchanged'}
    assert list(apps._path().parent.glob('*.tmp')) == []


def test_save_serialization_failure_does_not_leave_partial_settings(provider):
    apps._save(apps._state())
    previous = apps._path().read_bytes()
    with pytest.raises(TypeError):
        apps._save({'invalid': object()})
    assert apps._path().read_bytes() == previous
    assert list(apps._path().parent.glob('*.tmp')) == []


def test_save_invalidates_cache_only_when_requested(provider):
    data = apps._state()
    apps._tool_cache['sentinel'] = 'cached'
    apps._save(data, invalidate=False)
    assert apps._tool_cache == {'sentinel': 'cached'}
    apps._save(data)
    assert apps._tool_cache == {}


def test_lifecycle_pagination_secrets_and_resume(provider):
    result=apps.catalog('code','dev','page2')
    assert result['total_items']==1001 and result['next_cursor']=='next'
    assert provider['calls'][-1][2]['cursor']=='page2'
    link=apps.connect('github')
    assert not link['connected']
    assert apps.connect('github')==link
    assert len([c for c in provider['calls'] if c[1].endswith('/link')])==1
    assert apps.refresh()['connections'][0]['status']=='pending'
    provider['active']=True
    status=apps.refresh()
    assert status['connections'][0]['status']=='connected'
    for secret in ('test-project-key','ca_one','trs_one','redirect_url',provider['owner']):
        assert secret not in json.dumps(status)
    apps.set_enabled('github',False)
    assert not apps.tools()
    apps.set_enabled('github',True)
    assert len(apps.tools())==2
    apps.disconnect('github')
    assert apps.status()['connections'][0]['status']=='disconnected'
    assert not apps.tools()


@pytest.mark.parametrize('field,value',[('owner','different-user'),('account','ca_other')])
def test_other_account_cannot_become_connected(provider,field,value):
    apps.connect('github');provider['active']=True;provider[field]=value
    assert apps.refresh()['connections'][0]['status']!='connected'
    assert not apps.tools()


def test_expired_pending_needs_reconnect(provider):
    apps.connect('github'); data=apps._state();data['apps']['github']['linked_at']=0;apps._save(data)
    assert apps.refresh()['connections'][0]['status']=='needs_reconnect'


def test_disconnect_failure_disables_locally_and_can_retry(provider):
    connected(provider);provider['fail_delete']=True
    with pytest.raises(apps.AppError):apps.disconnect('github')
    assert apps.status()['connections'][0]['status']=='disconnect_pending'
    assert not apps.tools()
    provider['fail_delete']=False;apps.disconnect('github')
    assert apps.status()['connections'][0]['status']=='disconnected'


def test_tool_schema_and_cache(provider):
    connected(provider)
    defs=apps.tools()
    assert defs[0]['input_schema']['required']==['repo']
    apps.refresh();apps.tools()
    assert len([c for c in provider['calls'] if c[1].endswith('/tools')])==1
    assert all('COMPOSIO' not in t['name'] and 'SLACK' not in t['name'] for t in defs)


def test_actual_action_policy_and_account_are_used(provider):
    connected(provider)
    denied=apps.call('app__github__GITHUB_DELETE_REPOSITORY',{'repo':'test'})
    assert 'denied' in denied.lower() or 'blocked' in denied.lower()
    assert not any(c[1].endswith('/execute') for c in provider['calls'])
    result=json.loads(apps.call('app__github__GITHUB_GET_ISSUE',{'repo':'test'}))
    assert result['data']['ok']
    execution=[c for c in provider['calls'] if c[1].endswith('/execute')][-1]
    assert execution[3]['account']=='ca_one'
    assert execution[3]['enable_auto_workbench_offload'] is False
    with pytest.raises(apps.AppError):apps.call('app__github__GITHUB_GET_ISSUE',{})


def test_revalidation_blocks_revoked_account(provider):
    connected(provider);apps.tools();provider['active']=False
    with pytest.raises(apps.AppError):apps.call('app__github__GITHUB_GET_ISSUE',{'repo':'test'})
    assert not any(c[1].endswith('/execute') for c in provider['calls'])


def test_scope_cannot_be_bypassed_by_gateway(provider):
    connected(provider)
    subagent_scope.set_active('researcher')
    try: assert 'blocked' in apps.call('app__github__GITHUB_GET_ISSUE',{'repo':'test'}).lower()
    finally:subagent_scope.clear_active()
    assert not any(c[1].endswith('/execute') for c in provider['calls'])


def test_search_is_bounded_and_local_survives_missing_key(provider,monkeypatch):
    connected(provider)
    local=[{'name':f'mcp__local__get_{i}','description':'Read file','input_schema':{'type':'object'}} for i in range(50)]
    monkeypatch.setattr(mcp_client,'get_definitions',lambda:local)
    result=json.loads(app_tools.dispatch('search_connected_tools',{'query':'','limit':1000}))
    assert len(result['tools'])==20 and result['matches']==52
    monkeypatch.delenv('COMPOSIO_API_KEY')
    result=json.loads(app_tools.dispatch('search_connected_tools',{'query':'file'}))
    assert len(result['tools'])==8 and result['matches']==50


def test_pages_refuses_repeated_cursor(provider,monkeypatch):
    monkeypatch.setattr(apps,'_request',lambda *a,**kw:{'items':[], 'next_cursor':'again'})
    with pytest.raises(apps.AppError,match='pagination'):apps._pages('/tools',{})


def test_configure_verifies_before_persisting(provider):
    apps.configure('new-test-key')
    assert 'COMPOSIO_API_KEY=new-test-key' in (apps.ROOT/'.env').read_text()
    assert apps.status()['configured']


def test_catalog_keeps_valid_apps_when_provider_returns_invalid_identifiers(provider, monkeypatch):
    rows = [{'slug': 'github', 'name': 'GitHub'}, {'slug': 'bad/app'}, {'slug': None},
            {'name': 'No identifier'}, None, 'not-a-record', {'slug': 'gmail', 'name': 'Gmail', 'meta': 3}]
    monkeypatch.setattr(apps, '_request', lambda *a, **kw: {
        'items': rows, 'next_cursor': 'next-page', 'total_items': 1001})
    result = apps.catalog()
    assert [item['slug'] for item in result['items']] == ['github', 'gmail']
    assert result['skipped_items'] == 5
    assert result['next_cursor'] == 'next-page' and result['total_items'] == 1001
    assert apps.status()['configured'] and apps._state()['apps'] == {}


@pytest.mark.parametrize('rows', [[], [{'slug': '../private'}, {'slug': 'Bad App'}]])
def test_catalog_empty_or_all_invalid_page_keeps_pagination_and_key(provider, monkeypatch, rows):
    monkeypatch.setattr(apps, '_request', lambda *a, **kw: {'items': rows, 'next_cursor': 'next-page'})
    result = apps.catalog()
    assert result['items'] == [] and result['skipped_items'] == len(rows)
    assert result['next_cursor'] == 'next-page' and apps.status()['configured']


@pytest.mark.parametrize('rows', [None, {}, 'invalid'])
def test_catalog_rejects_an_invalid_page_shape_without_exposing_provider_text(provider, monkeypatch, rows):
    monkeypatch.setattr(apps, '_request', lambda *a, **kw: {'items': rows})
    with pytest.raises(apps.AppError, match='unexpected catalog response'):
        apps.catalog()
    assert apps.status()['configured']


@pytest.mark.parametrize('slug', ['bad/app', '../private', None, 'Bad App'])
def test_catalog_tolerance_does_not_relax_app_action_identifiers(provider, slug):
    for fn, args in ((apps.connect, (slug,)), (apps.set_enabled, (slug, True)), (apps.disconnect, (slug,))):
        with pytest.raises(apps.AppError, match='Invalid app identifier'):
            fn(*args)
    assert provider['calls'] == []


def test_routes_require_owner_origin_bounded_json(provider,monkeypatch):
    from dashboard import server
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server.config,'DASHBOARD_TOKEN','test-owner-token')
    client=TestClient(server.app)
    assert client.get('/apps').status_code==200
    assert client.get('/api/apps/status').status_code==401
    client.headers['Authorization']='Bearer test-owner-token'
    assert client.get('/api/apps/status').status_code==200
    assert client.post('/api/apps/github/connect',headers={'Origin':'https://other.test'}).status_code==403
    assert client.post('/api/apps/settings',json=[]).status_code==400
    assert client.post('/api/apps/settings',content='x'*16001).status_code==413
    assert client.post('/api/apps/github/connect').status_code==200
    assert client.get('/api/apps/github/tools').json()=={'items':[]}
    monkeypatch.setattr(server.config,'DASHBOARD_TOKEN','')
    assert client.get('/api/apps/status').status_code==403


def test_navigation_has_no_removed_targets():
    from pathlib import Path
    static=Path(__file__).resolve().parents[1]/'dashboard'/'static'
    html=(static/'index.html').read_text(encoding='utf-8')
    nav=html.split('<nav class="sidenav"')[1].split('</nav>')[0]
    for target in ('camera','subagents','phone','inbox','calendar'):
        assert f'data-tab="{target}"' not in nav
    assert 'href="/apps"' in nav and 'nav-advanced' in nav
    assert 'id="tab-camera"' not in html and 'avatar3d.js' not in html


def test_expired_session_is_recreated_without_reexecuting_actions(provider,monkeypatch):
    connected(provider)
    original=apps._request
    old={'expired':True}
    def request(method,path,**kw):
        if 'trs_one' in path and old['expired']:
            if method=='PATCH': old['expired']=False
            raise apps.AppError('Expired',404)
        return original(method,path,**kw)
    monkeypatch.setattr(apps,'_request',request)
    assert apps.refresh()['connections'][0]['status']=='connected'
    assert len([c for c in provider['calls'] if c[1]=='/tool_router/session'])==2
    assert not any(c[1].endswith('/execute') for c in provider['calls'])


def test_provider_outage_does_not_hide_local_tools(provider,monkeypatch):
    def fail(): raise apps.AppError('Provider offline')
    monkeypatch.setattr(apps,'tools',fail)
    local={'name':'mcp__local__get_file','description':'Read file','input_schema':{'type':'object'}}
    monkeypatch.setattr(mcp_client,'get_definitions',lambda:[local])
    result=json.loads(app_tools.dispatch('search_connected_tools',{'query':'file'}))
    assert result['warning']=='Provider offline' and result['tools'][0]['name']==local['name']
    assert json.loads(app_tools.dispatch('describe_connected_tool',{'name':local['name']}))==local


def test_agent_offers_only_small_discovery_surface_for_large_catalog(provider,monkeypatch):
    from agent.core import AgentCore
    from agent import self_mod
    monkeypatch.setattr(self_mod,'get_dynamic_tools',lambda:[])
    agent=AgentCore.__new__(AgentCore)
    agent._mcp_tools=[{'name':f'mcp__test__get_{i}'} for i in range(200)]
    names=[t['name'] for t in agent._all_tools()]
    assert all(t['name'] in names for t in app_tools.DEFINITIONS)
    assert not any(n.startswith('mcp__test__') for n in names)


def test_gateway_cannot_bypass_local_write_policy(provider,monkeypatch):
    name='mcp__local__delete_file'
    tool={'name':name,'description':'Delete file','input_schema':{'type':'object'}}
    monkeypatch.setattr(mcp_client,'get_definitions',lambda:[tool])
    monkeypatch.setattr(mcp_client,'_tool_registry',{name:('local','delete_file')})
    monkeypatch.setattr(mcp_client,'_run',lambda *_:pytest.fail('Write reached transport'))
    result=app_tools.dispatch('call_connected_tool',{'name':name,'arguments':{}})
    assert 'blocked' in result.lower() or 'denied' in result.lower()


@pytest.mark.parametrize('status_code',[401,402,403,429,500])
def test_http_errors_do_not_expose_provider_body(provider,monkeypatch,status_code):
    import httpx
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status_code,json={'secret':'must-not-leak'},request=req)))
    monkeypatch.setattr(apps.httpx,'Client',lambda **kw:client)
    with pytest.raises(apps.AppError) as err:
        HTTP_REQUEST('GET','/toolkits')
    assert 'must-not-leak' not in str(err.value) and err.value.status_code==status_code
