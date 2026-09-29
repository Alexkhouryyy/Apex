import base64
import json
from types import SimpleNamespace
import pytest
import config
from fastapi.testclient import TestClient
from agent import repository_hub as hub, skill_imports, skill_md, skill_forge, skills, approvals

SHA='a'*40
@pytest.fixture(autouse=True)
def isolated(test_db,tmp_path,monkeypatch):
    monkeypatch.setattr(skill_md,'_SKILLS_DIR',tmp_path/'procedures')
    monkeypatch.setattr(skill_md,'_USAGE_FILE',tmp_path/'procedures'/'.usage.json')
    monkeypatch.setattr(skills,'SKILLS_DIR',tmp_path/'code')
    monkeypatch.setattr(skills,'_registry',{})
    skill_forge.init_db()
    approvals.init_db()
    return tmp_path

@pytest.mark.parametrize('bad',['https://evil.example/o/r','https://github.com@evil.example/o/r','http://github.com/o/r','o/../r','o/r/tree/main','file:///repo','https://github.com/o/r?token=secret',None])
def test_repo_addresses_are_bounded_to_github(bad):
    with pytest.raises(ValueError):hub.parse_repo(bad)

def test_repo_scan_pins_and_discovers_without_installing(monkeypatch):
    calls=[]
    def remote(route,params=None):
        calls.append(route)
        if '/commits/' in route:return {'sha':SHA}
        return {'tree':[{'path':p,'mode':'100644','type':'blob'} for p in ['plugins/example/SKILL.md','README.md','package.json']]}
    monkeypatch.setattr(hub,'github',remote)
    monkeypatch.setattr(skill_imports,'_github',lambda *a:{'type':'file','encoding':'base64','content':base64.b64encode(b'Repository documentation').decode()})
    out=hub.inspect_repository('https://github.com/example/tools.git','main')
    assert out['revision']==SHA and out['skills']==['plugins/example']
    assert calls[-1].endswith(SHA)
    assert hub.inventory()[0]['id']==out['id']
    assert not skill_md._SKILLS_DIR.exists() and not skills.SKILLS_DIR.exists()

def test_truncated_scan_is_not_silently_complete(monkeypatch):
    monkeypatch.setattr(hub,'github',lambda route,params=None:{'sha':SHA} if '/commits/' in route else {'truncated':True})
    with pytest.raises(ValueError,match='incomplete'):hub.inspect_repository('o/r')
    assert hub.inventory()==[]

@pytest.mark.parametrize('folder',['plugins/a','.'])
def test_generic_skill_preview_install_and_disable(folder,monkeypatch):
    prefix='' if folder=='.' else folder+'/'
    def remote(repo,sha,p):
        assert repo=='example/tools' and sha==SHA
        if p==('' if folder=='.' else folder):return [{'type':'file','path':prefix+'SKILL.md'}]
        text='MIT License' if p=='LICENSE' else '---\nname: demo\ndescription: example\n---\nUse the existing tools.'
        return {'type':'file','encoding':'base64','content':base64.b64encode(text.encode()).decode()}
    monkeypatch.setattr(skill_imports,'_github',remote)
    p=skill_imports.preview('example/tools',SHA,folder)
    result=skill_imports.install(p['id'],'example','Uses Apex tools; no scripts or extra dependencies.')
    assert result['revision']==SHA
    assert skill_md.list_skills()[0]['name']=='example'
    skill_imports.set_enabled('example',False)
    assert skill_md.list_skills()==[]

def test_compile_validation_does_not_execute_module_level_code(isolated):
    marker=isolated/'executed'
    code=f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\ndef run(inputs): return "ok"'
    assert skill_forge._compile_check(code)[0]
    assert not marker.exists()
    assert not skill_forge._compile_check('run=lambda x: x')[0]

def test_improvement_is_staged_and_preserves_old_skill(monkeypatch):
    from agent import provider
    skills.SKILLS_DIR.mkdir()
    path=skills.SKILLS_DIR/'worker.py';path.write_text('def run(inputs): return "old"')
    monkeypatch.setattr(provider,'get_client',lambda model:object())
    monkeypatch.setattr(skill_forge,'_propose',lambda *args:{'name':'wrong_generated_name','description':'Improved','code':'def run(inputs): return "new"','input_schema':{'type':'object','properties':{}}})
    out=skill_forge.develop('Improve the output formatting','worker',False)
    assert 'STAGED' in out['result'] and 'old' in path.read_text()
    pending=approvals.list_pending()[0]
    assert pending['kind']=='skill_code'

def test_environment_requires_owner_and_checks_origin(monkeypatch):
    from dashboard.server import app
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','owner-test')
    with TestClient(app) as client:
        assert client.get('/api/environment').status_code==401
        client.headers['Authorization']='Bearer owner-test'
        assert client.get('/api/environment').status_code==200
        assert client.post('/api/environment/repositories',json={'source':'o/r'},headers={'Origin':'https://other.example'}).status_code==403
        assert client.post('/api/environment/repositories',json={'source':'file:///tmp'}).status_code==400
        assert client.post('/api/environment/develop',json={'description':'short'}).status_code==400
        assert client.post('/api/environment/develop',content='[]').status_code==400
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','')
    with TestClient(app) as client:
        assert client.get('/api/environment').status_code==403

def test_conversation_tools_route_to_the_same_workflows(monkeypatch):
    from agent import core
    monkeypatch.setattr(core.safety,'check',lambda *a:(True,''))
    monkeypatch.setattr(skill_forge,'develop',lambda *a:{'result':'staged'})
    assert json.loads(core._execute_tool_inner('develop_skill',{'description':'A reusable capability'}))['result']=='staged'
    monkeypatch.setattr(hub,'inspect_repository',lambda *a:{'status':'indexed'})
    assert json.loads(core._execute_tool_inner('repository_inspect',{'source':'o/r'}))['status']=='indexed'


def test_conversation_create_skill_is_pending_until_review(monkeypatch):
    from agent import core
    monkeypatch.setattr(core.safety,'check',lambda *a:(True,''))
    out=core._execute_tool_inner('create_skill',{'name':'new_helper','description':'Compute a result','code':'def run(inputs): return "ok"'})
    assert 'STAGED' in out
    assert not (skills.SKILLS_DIR/'new_helper.py').exists()


def test_update_does_not_merge_diverged_checkouts(monkeypatch):
    from agent import control
    calls=[]
    monkeypatch.setattr(control,'update_status',lambda:{'can_update':True,'branch':'main'})
    def git(*args,**kwargs):
        calls.append(args)
        return (1,'Cannot fast-forward') if args[0]=='pull' else (0,'old')
    monkeypatch.setattr(control,'_git',git)
    assert not control.do_update()['ok']
    assert ('pull','--ff-only','origin','main') in calls


def test_discuss_can_draft_skills_but_cannot_execute_them():
    from agent import companion
    assert {'develop_skill','repository_inspect','list_skills'} <= companion.DISCUSS_TOOLS
    assert not {'run_skill','bash','create_skill','skill_manage'} & companion.DISCUSS_TOOLS


def test_sandbox_wrapper_preserves_multiline_code_and_json_values(monkeypatch):
    import ast
    from tools import sandbox
    values={'enabled':True,'optional':None,'items':[False,'Unicode café']}
    class Backend:
        def run_python(self,script,timeout):
            tree=ast.parse(script)
            decode=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='loads')
            assert json.loads(ast.literal_eval(decode.args[0]))==values
            run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
            assert isinstance(run.body[0],ast.If)
            return {'stdout':'{"ok":true,"result":"checked"}','returncode':0}
    monkeypatch.setattr(sandbox,'autonomous_backend',lambda:Backend())
    ok,out=skill_forge._validate_in_sandbox('def run(inputs):\n    if inputs["enabled"]:\n        return "yes"\n    return "no"',values)
    assert ok and out=='checked'


def test_large_existing_skill_is_not_silently_truncated(monkeypatch):
    skills.SKILLS_DIR.mkdir()
    (skills.SKILLS_DIR/'large.py').write_text('#'+'x'*20001)
    with pytest.raises(ValueError,match='too large'):
        skill_forge.develop('Improve the large existing skill','large')


def test_forged_registration_failure_is_reported_to_browser(monkeypatch):
    from dashboard.server import forged_tools_approve
    monkeypatch.setattr(skill_forge, 'approve_forged', lambda tool_id: 'Registration failed; tool remains pending: invalid code')
    response = forged_tools_approve(1)
    assert response.status_code == 409
    assert 'remains pending' in json.loads(response.body)['error']
