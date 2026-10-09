"""An idea can start in a new folder without shell preparation or overwriting user work."""
from pathlib import Path

import pytest

from agent import code_studio as code, code_engines
from tests.test_code_studio import lab, git, wait


def test_new_idea_prepares_git_and_isolates_generated_work(lab, monkeypatch):
    folder = lab.root.parent / 'fresh idea'
    p = code.create_project(str(folder), 'Fresh idea')
    assert p['branch'] == 'main'
    assert git(folder, 'status', '--porcelain') == ''
    assert git(folder, 'log', '--format=%s', '-1').strip() == 'Create project in Apex Code'
    def build(engine, text, worktree, *args, **kwargs):
        (worktree / 'app.py').write_text('print("new idea")\n')
        return {'status': 'done', 'summary': 'Created app'}
    monkeypatch.setattr(code_engines, 'turn', build)
    sid = code.start(p['id'], 'Build an idea')['id']
    wait(sid)
    assert not (folder / 'app.py').exists()
    assert (Path(code.session(sid)['worktree']) / 'app.py').exists()


def test_existing_folder_and_nested_git_are_rejected(lab):
    before = git(lab.root, 'status', '--porcelain')
    with pytest.raises(code.CodeError, match='already exists'):
        code.create_project(str(lab.root))
    with pytest.raises(code.CodeError, match='outside an existing Git'):
        code.create_project(str(lab.root / 'nested'))
    assert git(lab.root, 'status', '--porcelain') == before
    assert not (lab.root / 'nested').exists()


@pytest.mark.parametrize('path', ['', 'relative-folder'])
def test_new_project_requires_an_absolute_path(test_db, path):
    with pytest.raises(code.CodeError, match='full path'):
        code.create_project(path)


def test_new_project_missing_parent_is_explained(tmp_path, test_db):
    folder = tmp_path / 'missing' / 'idea'
    with pytest.raises(code.CodeError, match='parent folder'):
        code.create_project(str(folder))
    assert not folder.exists()
