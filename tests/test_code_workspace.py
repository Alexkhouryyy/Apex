"""Real Git/file operations without any coding account or model request."""
import sys
import os
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent import code_studio as studio, code_engines
from tests.test_code_studio import lab as base_lab, git


@pytest.fixture
def lab(base_lab):
    # Git for Windows often defaults to autocrlf=true. These fixture baselines
    # use LF explicitly; the CRLF test below supplies Windows bytes itself.
    git(base_lab.root,'config','core.autocrlf','false')
    return base_lab


def test_workspace_opens_without_calling_a_provider(lab, monkeypatch):
    monkeypatch.setattr(code_engines, 'turn', lambda *a, **k: pytest.fail('Workspace launched a model'))
    s = studio.open_workspace(lab.pid)
    assert not s['working'] and s['status'] == 'ready'
    assert studio.read_file('README.md', s['id'])['text'] == 'hello\n'
    assert not lab.calls('claude') and not lab.calls('codex')


@pytest.mark.parametrize('previous', [studio.PYTEST_CHECKS, 'python custom_checks.py', ''])
def test_apex_checks_upgrade_preserves_custom_commands(lab, monkeypatch, previous):
    studio.update_project(lab.pid, checks=previous)
    monkeypatch.setattr(studio, 'APEX_ROOT', lab.root)
    expected = studio.APEX_CHECKS if previous == studio.PYTEST_CHECKS else previous
    assert studio.project(lab.pid)['checks'] == expected
    assert studio.project(lab.pid)['checks'] == expected


def test_generic_python_project_keeps_its_own_runner(lab, monkeypatch):
    (lab.root / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    assert studio._default_checks(lab.root) == studio.PYTEST_CHECKS
    monkeypatch.setattr(studio, 'APEX_ROOT', lab.root)
    assert studio._default_checks(lab.root) == studio.APEX_CHECKS


def test_isolated_launcher_requires_test_counts_for_proof():
    project = {'checks': studio.APEX_CHECKS}
    assert studio._check_result(project, 0, 'nothing ran', False, False)[0] == 'unknown'
    assert studio._check_result(project, 0, '1 passed in 0.1s', False, False)[0] == 'passed'
    assert studio._check_result(project, 1, '1 failed in 0.1s', False, False)[0] == 'failed'


def test_apex_run_checks_uses_session_files_and_isolated_settings(lab, monkeypatch):
    from tests.test_code_studio import wait
    launcher = (studio.APEX_ROOT / 'scripts' / 'run_tests.py').read_bytes()
    (lab.root / 'scripts').mkdir()
    (lab.root / 'scripts' / 'run_tests.py').write_bytes(launcher)
    (lab.root / 'config.py').write_text('import os\nMODEL = os.getenv("AGENT_MODEL")\n', encoding='utf-8')
    (lab.root / 'marker.txt').write_text('main', encoding='utf-8')
    (lab.root / 'test_isolated.py').write_text('''import os
from pathlib import Path
from dotenv import load_dotenv
def test_isolated():
    assert Path("marker.txt").read_text(encoding="utf-8") == "session"
    assert load_dotenv() is False
    assert os.getenv("AGENT_MODEL") is None
    assert os.getenv("DEEPSEEK_API_KEY") is None
    assert os.getenv("PYTHON_DOTENV_DISABLED") == "1"
    assert Path(os.environ["DB_PATH"]).parent != Path.cwd()
''', encoding='utf-8')
    git(lab.root, 'add', '-A'); git(lab.root, 'commit', '-q', '-m', 'isolated checks')
    studio.update_project(lab.pid, checks=studio.PYTEST_CHECKS)
    monkeypatch.setattr(studio, 'APEX_ROOT', lab.root)
    monkeypatch.setenv('AGENT_MODEL', 'owner-model')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'owner-key-for-test')
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''))
    sid = studio.open_workspace(lab.pid)['id']
    marker = studio.read_file('marker.txt', sid)
    studio.write_file(sid, 'marker.txt', 'session', marker['revision'])
    studio.run_checks(sid)
    result = wait(sid, timeout=45)
    assert result['check_state'] == 'passed', result['check_output']
    assert result['check_evidence'] == '1 passed'
    assert (lab.root / 'marker.txt').read_text(encoding='utf-8') == 'main'
    assert os.environ['AGENT_MODEL'] == 'owner-model'


def test_handoff_keeps_original_requirements_after_many_long_followups(lab):
    s=studio.open_workspace(lab.pid)
    original='Build a working offline project. '+('Detailed requirement. '*100)+'Use SQLite, export CSV, keep data after restart.'
    studio.event(s['id'],'you',text=original)
    for i in range(8):
        studio.event(s['id'],'you',text=f'Follow-up {i}: '+('More detail. '*100)+f'Acceptance check {i}.')
    recap=studio._recap(s,'Finish the export and test it.')
    assert original in recap
    assert 'Acceptance check 7.' in recap and 'Acceptance check 2.' in recap
    assert 'Acceptance check 0.' not in recap
    assert 'later owner instructions supersede' in recap
    assert recap.endswith('Finish the export and test it.')


def test_edit_create_rename_delete_undo_and_keep(lab):
    sid = studio.open_workspace(lab.pid)['id']
    f = studio.read_file('README.md', sid)
    saved = studio.write_file(sid, f['path'], 'hello from the editor\n', f['revision'])
    assert saved['text'] == 'hello from the editor\n'
    assert (lab.root / 'README.md').read_text() == 'hello\n'
    new = studio.write_file(sid, 'src/main.py', "print('works')\n", create=True)
    renamed = studio.rename_file(sid, new['path'], 'src/app.py', new['revision'])
    studio.delete_file(sid, renamed['path'], renamed['revision'])
    studio.undo(sid)
    assert studio.read_file('src/app.py', sid)['text'] == "print('works')\n"
    studio.keep(sid)
    assert (lab.root / 'src/app.py').is_file()
    assert (lab.root / 'README.md').read_text() == 'hello from the editor\n'
    assert git(lab.root, 'status', '--porcelain') == ''


def test_stale_editor_does_not_overwrite_an_external_edit(lab):
    s = studio.open_workspace(lab.pid)
    f = studio.read_file('README.md', s['id'])
    target = Path(s['worktree']) / 'README.md'
    target.write_text('changed by another editor\n')
    with pytest.raises(studio.FileConflict, match='changed on disk'):
        studio.write_file(s['id'], f['path'], 'old draft\n', f['revision'])
    assert target.read_text() == 'changed by another editor\n'


@pytest.mark.parametrize('path', ['../outside.py', '/tmp/outside.py', '.git/config', 'x/.GIT/config', 'C:/x.py',
                                 'src/CON.txt', 'src/a:stream', 'src/a.', 'src//x.py', 'src/../x.py'])
def test_file_writes_refuse_escape_git_internals_and_windows_aliases(lab, path):
    sid = studio.open_workspace(lab.pid)['id']
    with pytest.raises(studio.CodeError):
        studio.write_file(sid, path, 'bad', create=True)


def test_links_cannot_be_followed_by_the_editor(lab, tmp_path):
    s = studio.open_workspace(lab.pid)
    outside = tmp_path / 'outside'; outside.mkdir()
    link = Path(s['worktree']) / 'linked'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('OS denies symlink creation')
    with pytest.raises(studio.CodeError, match='link'):
        studio.write_file(s['id'], 'linked/a.py', 'bad', create=True)
    assert not (outside / 'a.py').exists()


def test_editor_preserves_crlf_and_executable_mode(lab):
    (lab.root / 'win.py').write_bytes(b'first\r\nsecond\r\n')
    git(lab.root, 'add', 'win.py'); git(lab.root, 'commit', '-q', '-m', 'Windows file')
    s = studio.open_workspace(lab.pid)
    f = studio.read_file('win.py', s['id'])
    studio.write_file(s['id'], f['path'], 'first\nchanged\n', f['revision'])
    assert (Path(s['worktree']) / 'win.py').read_bytes() == b'first\r\nchanged\r\n'


@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_owner_can_explicitly_snapshot_uncommitted_files(lab, newline):
    readme = 'my current work' + newline
    new_file = 'current = True' + newline
    # Explicit bytes avoid the host OS translating the test's line endings.
    (lab.root / 'README.md').write_bytes(readme.encode('utf-8'))
    (lab.root / 'new.py').write_bytes(new_file.encode('utf-8'))
    git(lab.root, 'add', 'README.md')
    before = git(lab.root, 'diff', '--cached')
    s = studio.open_workspace(lab.pid, include_changes=True)
    assert studio.read_file('README.md', s['id'])['text'] == readme
    assert studio.read_file('new.py', s['id'])['text'] == new_file
    assert git(lab.root, 'diff', '--cached') == before
    assert not lab.calls('claude')
    assert any('uncommitted files' in e.get('text', '') for e in studio.events(s['id']))


def test_default_workspace_still_starts_from_the_commit(lab):
    (lab.root / 'README.md').write_text('dirty\n')
    s = studio.open_workspace(lab.pid)
    assert studio.read_file('README.md', s['id'])['text'] == 'hello\n'
    assert (lab.root / 'README.md').read_text() == 'dirty\n'


def test_mutations_are_owner_only_origin_checked_and_conflicts_are_409(lab, monkeypatch):
    import config
    from dashboard.code import router
    app = FastAPI()
    @app.middleware('http')
    async def owner(request, call_next):
        request.state.is_master = request.headers.get('Authorization') == 'Bearer master'
        return await call_next(request)
    app.include_router(router)
    client = TestClient(app)
    sid = studio.open_workspace(lab.pid)['id']
    f = studio.read_file('README.md', sid)
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'master')
    body = {'path':'README.md', 'text':'updated\n', 'revision':f['revision']}
    endpoint = f'/api/code/sessions/{sid}/file'
    assert client.put(endpoint, json=body).status_code == 403
    assert client.put(endpoint, headers={'Authorization':'Bearer master','Origin':'https://evil.example'},json=body).status_code == 403
    assert client.put(endpoint, headers={'Authorization':'Bearer master'},json=body).status_code == 200
    assert client.put(endpoint, headers={'Authorization':'Bearer master'},json=body).status_code == 409


def test_git_checkpoint_failure_returns_the_saved_revision_and_warning(lab, monkeypatch):
    sid=studio.open_workspace(lab.pid)['id']
    original=studio.read_file('README.md',sid)
    def locked(*a,**k):
        raise studio.CodeError('Git index is locked')
    monkeypatch.setattr(studio,'_checkpoint',locked)
    saved=studio.write_file(sid,'README.md','saved on disk\n',original['revision'])
    assert saved['text']=='saved on disk\n' and saved['revision']!=original['revision']
    assert 'Undo checkpoint failed' in saved['warning']
    again=studio.write_file(sid,'README.md','second save\n',saved['revision'])
    assert again['text']=='second save\n'


def test_crlf_expansion_cannot_save_an_unreadable_oversize_file(lab, monkeypatch):
    sid=studio.open_workspace(lab.pid)['id']
    target=Path(studio.session(sid)['worktree'])/'README.md'
    target.write_bytes(b'first\r\n')
    f=studio.read_file('README.md',sid)
    monkeypatch.setattr(studio,'FILE_LIMIT',20)
    with pytest.raises(studio.CodeError,match='line endings'):
        studio.write_file(sid,'README.md','a\n'*9,f['revision'])
    assert target.read_bytes()==b'first\r\n'


def test_native_shell_api_checks_owner_origin_and_real_shutdown(lab, monkeypatch):
    import config
    from agent import code_terminal
    from dashboard.code import router
    app=FastAPI()
    @app.middleware('http')
    async def owner(request,call_next):
        request.state.is_master=request.headers.get('Authorization')=='Bearer master'
        return await call_next(request)
    app.include_router(router)
    client=TestClient(app)
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','master')
    sid=studio.open_workspace(lab.pid)['id'];base=f'/api/code/sessions/{sid}'
    owner={'Authorization':'Bearer master'}
    foreign={**owner,'Origin':'https://evil.example'}
    for verb,suffix,body in [('post','/shell',{}),('post','/shell-input',{'text':'echo hi\r'}),
                              ('post','/shell-resize',{'rows':24,'cols':80}),('delete','/shell',{})]:
        assert client.request(verb,base+suffix,json=body).status_code==403
        assert client.request(verb,base+suffix,headers=foreign,json=body).status_code==403
    assert client.get(base+'/shell').status_code==403
    try:
        assert client.post(base+'/shell',headers=owner,json={}).status_code==200
        assert client.post(base+'/shell-input',headers=owner,json={'text':'echo shell-ready\r'}).status_code==200
        assert client.post(base+'/shell-resize',headers=owner,json={'rows':0,'cols':80}).status_code==400
        studio.discard(sid)
        assert not code_terminal.read(sid)['running']
        assert client.post(base+'/shell',headers=owner,json={}).status_code==400
    finally:
        code_terminal.stop(sid)
