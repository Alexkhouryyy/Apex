"""Exercise real plugin imports and calls in temporary packages, not mock registries."""
import io
import json
import zipfile
import pytest
import yaml
from agent import plugins, longterm


SHA = 'a' * 40
CODE = '''
def register(ctx):
    ctx.register_tool(name='echo', schema={'name':'echo', 'description':'Echo text', 'parameters':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}, handler=lambda args: ctx.get_config('prefix', '') + args['text'])
    ctx.register_command('status', lambda args: 'status:' + args)
'''


@pytest.fixture(autouse=True)
def isolated(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, 'ROOT', tmp_path/'plugins')
    monkeypatch.setattr(plugins, '_cache', {})
    monkeypatch.setattr(plugins, '_errors', {})
    monkeypatch.setattr(plugins, '_failed', {})
    monkeypatch.setattr(longterm, '_embed', lambda *args: None)
    monkeypatch.setattr(plugins.repository_hub, 'github', lambda *args: {'sha': SHA})
    return tmp_path


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z:
        for p, content in files.items():
            z.writestr('repo-sha/' + p, content)
    return stream.getvalue()


def review(monkeypatch, code=CODE, manifest=None, subdir='', extra=None):
    manifest = manifest or {'name':'sample','version':'1.0','provides_tools':['echo'], 'config_schema':{'prefix':{'type':'str','default':''}}}
    prefix = subdir + '/' if subdir else ''
    files = {prefix+'plugin.yaml': yaml.safe_dump(manifest), prefix+'__init__.py': code}
    files.update(extra or {})
    raw = archive(files)
    monkeypatch.setattr(plugins, '_download', lambda *args: raw)
    return plugins.preview('owner/repo', 'main', subdir)


def activate(monkeypatch, code=CODE, manifest=None):
    p = review(monkeypatch, code, manifest)
    plugins.install(p['id'])
    plugins.set_enabled(p['manifest']['name'], True, True)
    return p


def tool(name='echo'):
    return next(d['name'] for d in plugins.definitions() if d['description'] == 'Echo text')


def test_review_and_disabled_install_never_import_code(monkeypatch, isolated):
    marker=isolated/'side_effect'
    p=review(monkeypatch, f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n'+CODE)
    assert not marker.exists()
    plugins.install(p['id'])
    assert plugins.definitions()==[] and not marker.exists()
    with pytest.raises(ValueError, match='trust'):plugins.set_enabled('sample',True)
    plugins.set_enabled('sample',True,True)
    assert marker.exists()
    assert plugins.call(tool(),{'text':'works'})=='works'
    plugins.set_enabled('sample',False)
    assert plugins.definitions()==[]


def test_settings_validation_reload_and_schema_validation(monkeypatch):
    p=review(monkeypatch)
    plugins.install(p['id'])
    with pytest.raises(ValueError):plugins.configure('sample',{'prefix':42})
    plugins.configure('sample',{'prefix':'Hi '})
    plugins.set_enabled('sample',True,True)
    assert plugins.call(tool(),{'text':'there'})=='Hi there'
    assert '[Plugin error]' in plugins.call(tool(),{'wrong':'x'})
    with pytest.raises(ValueError,match='Disable'):plugins.configure('sample',{'prefix':'new'})
    plugins.set_enabled('sample',False)
    plugins.configure('sample',{'prefix':'new '})
    plugins.set_enabled('sample',True,True)
    assert plugins.call(tool(),{'text':'there'})=='new there'


def test_update_and_rollback_preserve_old_package_and_disable(monkeypatch):
    first=activate(monkeypatch)
    second=review(monkeypatch, CODE.replace("+ args['text']", "+ 'v2:' + args['text']"))
    with pytest.raises(ValueError):plugins.install(second['id'])
    plugins.install(second['id'],first['digest'])
    assert plugins.definitions()==[]
    plugins.set_enabled('sample',True,True)
    assert plugins.call(tool(),{'text':'x'})=='v2:x'
    plugins.change('sample','rollback')
    assert plugins.definitions()==[]
    plugins.set_enabled('sample',True,True)
    assert plugins.call(tool(),{'text':'x'})=='x'
    plugins.change('sample','remove')
    assert plugins.inventory()['plugins']==[] and plugins.definitions()==[]


def test_changed_review_and_installed_source_are_refused(monkeypatch):
    p=review(monkeypatch)
    path=plugins._folder(p['id'])/'__init__.py'
    path.write_text(CODE+'\n# edited')
    with pytest.raises(ValueError,match='changed'):plugins.install(p['id'])
    p=activate(monkeypatch)
    name=tool()
    (plugins._folder(p['id'])/'__init__.py').write_text(CODE+'\n# edited')
    assert plugins.definitions()==[]
    assert 'unavailable' in plugins.call(name,{'text':'x'})


def test_registration_failure_does_not_publish_partial_tools(monkeypatch):
    p=review(monkeypatch,CODE+"\n    raise RuntimeError('failed after registering')\n")
    plugins.install(p['id'])
    with pytest.raises(ValueError,match='failed after'):plugins.set_enabled('sample',True,True)
    assert plugins.definitions()==[] and not plugins.inventory()['plugins'][0]['enabled']


@pytest.mark.parametrize('path',['../escape','/absolute','x/../../outside','x\\bad','nul.py','x:a','a.','a/CON.txt'])
def test_archive_rejects_unsafe_windows_paths(path,isolated):
    raw=archive({path:'bad'})
    # zipfile normalizes backslashes when writing on Windows; inject the original
    # ZIP header spelling to exercise archives produced on other platforms.
    if '\\' in path:raw=raw.replace(path.replace('\\','/').encode(),path.encode())
    with pytest.raises(ValueError):plugins._extract(raw,'',isolated/'out')


def test_archive_rejects_symlink_and_case_collision(isolated):
    with pytest.raises(ValueError,match='duplicate'):plugins._extract(archive({'A.py':'a','a.py':'b'}),'',isolated/'out')
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        entry=zipfile.ZipInfo('root/link');entry.external_attr=0o120777<<16;z.writestr(entry,'outside')
    with pytest.raises(ValueError,match='links'):plugins._extract(raw.getvalue(),'',isolated/'out')


def test_subdirectory_and_relative_python_imports(monkeypatch):
    p=review(monkeypatch,'from .helper import register',subdir='plugins/sample',extra={'plugins/sample/helper.py':CODE,'unrelated.py':'raise RuntimeError()'})
    assert [f['path'] for f in p['files']]==['__init__.py','helper.py','plugin.yaml']
    plugins.install(p['id']);plugins.set_enabled('sample',True,True)
    assert plugins.call(tool(),{'text':'relative'})=='relative'


def test_missing_dependencies_and_hermes_features_do_not_enable(monkeypatch):
    m={'name':'sample','python_dependencies':['apex-nonexistent-fixture-package>=1'],'provides_hooks':['on_session_start']}
    p=review(monkeypatch,manifest=m)
    assert any('dependency' in i for i in p['issues']) and any('hook' in i for i in p['issues'])
    plugins.install(p['id'])
    with pytest.raises(ValueError,match='Unsupported'):plugins.set_enabled('sample',True,True)
    assert plugins.definitions()==[]


def test_hooks_can_block_but_cannot_modify_inputs(monkeypatch):
    code=CODE+'''
    def before(tool_name, args, **kwargs):
        args['text']='tampered'
        return {'action':'block','message':'fixture refusal'}
    ctx.register_hook('pre_tool_call', before)
'''
    activate(monkeypatch,code)
    inputs={'text':'original'}
    assert 'fixture refusal' in plugins.emit('pre_tool_call',tool_name='x',args=inputs)
    assert inputs['text']=='original'


def test_dispatch_obeys_safety_and_subagent_scope(monkeypatch):
    from agent import core, subagent_scope
    activate(monkeypatch)
    name=tool()
    monkeypatch.setattr(core.safety,'check',lambda *a:(False,'denied'))
    assert 'BLOCKED' in core._execute_tool_inner(name,{'text':'x'})
    monkeypatch.setattr(core.safety,'check',lambda *a:(True,''))
    assert core._execute_tool_inner(name,{'text':'x'})=='x'
    subagent_scope.set_active('researcher')
    try:assert 'Blocked' in core._execute_tool_inner(name,{'text':'x'})
    finally:subagent_scope.clear_active()


def test_commands_and_bundled_skills_are_real_callable_tools(monkeypatch):
    p=review(monkeypatch,extra={'skills/howto/SKILL.md':'Use this procedure.'})
    plugins.install(p['id']);plugins.set_enabled('sample',True,True)
    assert plugins.call(*plugins.slash('/sample:status hello'))=='status:hello'
    skill=next(d for d in plugins.definitions() if d['name'].endswith('__skills'))
    assert plugins.call(skill['name'],{'name':'howto'})=='Use this procedure.'
    assert '[Plugin error]' in plugins.call(skill['name'],{'name':'../../outside'})


def test_memory_provider_routes_remember_recall_and_disable_restores_builtin(monkeypatch):
    code='''
def register(ctx):
    rows=[]
    def remember(content, **kwargs):
        rows.append({'content':content})
        return 'stored in plugin'
    def recall(**kwargs): return list(rows)
    ctx.register_memory_provider('notes',recall=recall,remember=remember)
'''
    activate(monkeypatch,code,{'name':'sample'})
    plugins.select_provider('memory','sample:notes')
    assert longterm.remember('hello')=='stored in plugin'
    assert longterm.recall('hello')==[{'content':'hello'}]
    plugins.set_enabled('sample',False)
    assert plugins.inventory()['selected']['memory']==''
    assert longterm.recall('hello')==[]


def test_context_engine_receives_old_messages_preserves_recent_and_fails_closed(monkeypatch):
    from agent.memory import Memory
    code='''
def register(ctx):
    def summarize(messages, summary):
        return 'compressed:' + str(len(messages))
    ctx.register_context_engine('compact',summarize=summarize)
'''
    p=activate(monkeypatch,code,{'name':'sample'})
    plugins.select_provider('context','sample:compact')
    m=Memory()
    for i in range(32):m.add_user(str(i))
    m.maybe_summarize(None)
    assert m.summary=='compressed:20' and len(m.messages)==12
    (plugins._folder(p['id'])/'__init__.py').write_text('changed')
    for i in range(20):m.add_user(str(i))
    m.maybe_summarize(None)
    assert len(m.messages)==32 and m.summary=='compressed:20'


def test_bundled_plugin_observes_real_dispatch(monkeypatch):
    from agent import core
    monkeypatch.setattr(core.safety,'check',lambda *a:(True,''))
    p=plugins.preview_bundled('session-insights')
    plugins.install(p['id']);plugins.set_enabled('session-insights',True,True)
    name=next(d['name'] for d in plugins.definitions() if d['name'].find('__t_')>=0)
    first=json.loads(core._execute_tool(name,{}))
    second=json.loads(core._execute_tool(name,{}))
    assert second['total_calls']==first['total_calls']+1
    assert second['by_tool'][name]==1


def test_owner_and_origin_required_for_plugin_writes(monkeypatch):
    from dashboard.server import app
    from fastapi.testclient import TestClient
    import config
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','owner-test')
    with TestClient(app) as client:
        assert client.post('/api/environment/plugins/bundled',json={'name':'session-insights'}).status_code==401
        client.headers['Authorization']='Bearer owner-test'
        assert client.post('/api/environment/plugins/bundled',json={'name':'session-insights'},headers={'Origin':'https://evil.example'}).status_code==403
        p=client.post('/api/environment/plugins/bundled',json={'name':'session-insights'}).json()
        assert client.post('/api/environment/plugins/install',json={'id':p['id']}).status_code==200
        assert client.post('/api/environment/plugins/enabled',json={'name':'session-insights','enabled':True}).status_code==400
        assert client.post('/api/environment/plugins/enabled',json={'name':'session-insights','enabled':True,'trust':True}).status_code==200
        assert client.get('/api/environment/plugins').json()['plugins'][0]['enabled']


def test_stale_update_cannot_replace_newer_install(monkeypatch):
    first=activate(monkeypatch)
    second=review(monkeypatch,CODE+'\n# second')
    third=review(monkeypatch,CODE+'\n# third')
    plugins.install(second['id'],first['digest'])
    with pytest.raises(ValueError,match='changed'):plugins.install(third['id'],first['digest'])
    assert plugins.inventory()['plugins'][0]['package']['id']==second['id']


def test_tool_names_do_not_collide_on_common_prefix(monkeypatch):
    code='''
def register(ctx):
    for name in ['same_really_long_prefix_one','same_really_long_prefix_two']:
        ctx.register_tool(name=name,schema={'description':name,'parameters':{'type':'object'}},handler=lambda args:'ok')
'''
    activate(monkeypatch,code,{'name':'sample'})
    names=[d['name'] for d in plugins.definitions()]
    assert len(set(names))==2 and all(len(n)<=64 for n in names)


def test_enabled_package_reloads_after_process_cache_is_cleared(monkeypatch):
    activate(monkeypatch)
    before=tool()
    plugins._cache.clear()
    assert plugins.call(before,{'text':'after restart'})=='after restart'
    assert plugins.inventory()['plugins'][0]['loaded']


def test_async_handlers_and_sync_registration_requirement(monkeypatch):
    code='''
async def echo(args): return args['text']
def register(ctx):
    ctx.register_tool(name='echo',schema={'description':'Echo text','parameters':{'type':'object'}},handler=echo)
'''
    p=activate(monkeypatch,code,{'name':'sample'})
    assert plugins.call(tool(),{'text':'async'})=='async'
    p2=review(monkeypatch,'async def register(ctx): pass',{'name':'sample'})
    plugins.install(p2['id'],p['digest'])
    with pytest.raises(ValueError,match='synchronous'):plugins.set_enabled('sample',True,True)


def test_failed_startup_is_not_reimported_on_every_tool_call(monkeypatch):
    code='''
from pathlib import Path
def register(ctx):
    marker=ctx.data_dir/'attempts'
    previous=marker.read_text() if marker.exists() else ''
    marker.write_text(previous+'x')
    if previous: raise RuntimeError('restarted fixture failure')
'''
    activate(monkeypatch,code,{'name':'sample'})
    plugins._cache.clear()
    assert plugins.definitions()==[]
    assert plugins.definitions()==[]
    assert (plugins.ROOT/'data'/'sample'/'attempts').read_text()=='xx'


def test_manifest_aliases_and_oversized_archive_entries_rejected(monkeypatch,isolated):
    raw=archive({'plugin.yaml':'name: sample\nx: &a [1, 2]\ny: *a','__init__.py':CODE})
    monkeypatch.setattr(plugins,'_download',lambda *args:raw)
    with pytest.raises(ValueError,match='aliases'):plugins.preview('owner/repo')
    raw=archive({'large':'x'*5_000_001})
    with pytest.raises(ValueError,match='size limit'):plugins._extract(raw,'',isolated/'large')
