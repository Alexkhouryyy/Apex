"""Real Git + Python protocol fakes, including on Windows.

This checks the complete Code workflow without paid accounts. The protocol
fakes from the original suite are launched through Python, avoiding POSIX
shebangs; command permissions have separate tests in test_code_boundaries.
"""
import sys
from pathlib import Path

import pytest

from agent import code_engines, code_studio, work_engines
from tests.test_code_studio import lab, git, wait


@pytest.mark.parametrize('engine', ['claude', 'chatgpt'])
def test_branch_checks_review_and_keep_with_portable_protocol_fake(lab, monkeypatch, engine):
    original = code_engines.command
    monkeypatch.setattr(work_engines, 'binary', lambda e: str(lab.bins / work_engines.COMMANDS[e]))
    monkeypatch.setattr(work_engines, 'check', lambda e: {'ok': True, 'why': '', 'how': ''})
    def python_fake(e, exe, folder, mode, resume=None, options=None):
        return [sys.executable, exe, *original(e, exe, folder, mode, resume, options)[1:]]
    monkeypatch.setattr(code_engines, 'command', python_fake)
    filename = 'step1.py' if engine == 'claude' else 'codex1.py'
    (lab.root / 'check.py').write_text(f"from pathlib import Path\nassert Path({filename!r}).is_file()\nprint('1 passed')\n")
    git(lab.root, 'add', 'check.py')
    git(lab.root, 'commit', '-q', '-m', 'add acceptance check')
    code_studio.update_project(lab.pid, checks=f'"{sys.executable}" check.py')
    s = code_studio.start(lab.pid, 'Add a script', engine)
    s = wait(s['id'])
    assert s['last_status'] == 'done'
    assert not (lab.root / filename).exists()
    assert (Path(s['worktree']) / filename).is_file()
    code_studio.run_checks(s['id'])
    s = wait(s['id'])
    assert s['check_state'] == 'passed'
    assert code_studio.proof(s['id'])['verdict'] == 'proved'
    other = 'chatgpt' if engine == 'claude' else 'claude'
    lab.mode(work_engines.COMMANDS[other], 'review')
    code_studio.review(s['id'], other)
    s = wait(s['id'])
    assert s['review_state'] == 'done' and s['review_rating'] == 6
    code_studio.keep(s['id'], require_proof=True)
    assert code_studio.session(s['id'])['status'] == 'kept'
    assert (lab.root / filename).is_file()
    assert git(lab.root, 'status', '--porcelain') == ''
    assert not Path(s['worktree']).exists()
    assert s['id'] not in code_studio._operations
