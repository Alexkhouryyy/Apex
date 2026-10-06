"""Contract checks against the REAL Claude Code and Codex tools, when they are
installed (skipped otherwise, e.g. in CI). Nothing here uses any plan: only
`--help`, `--version` and the sign-in status commands are run, and Codex is
pointed at an empty, throwaway home so your own sign-in is never touched.

Point APEX_REAL_CODEX at a codex binary to check one that isn't on PATH."""
import os
import shutil
import subprocess

import pytest

from agent import work_engines
from scripts import work_plans_check

CODEX = os.environ.get('APEX_REAL_CODEX') or shutil.which('codex')
CLAUDE = shutil.which('claude')


@pytest.fixture
def real_codex(tmp_path, monkeypatch):
    if not CODEX:
        pytest.skip('Codex is not installed here')
    monkeypatch.setenv('PATH', f"{os.path.dirname(CODEX)}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    (tmp_path / 'codex-home').mkdir()
    work_engines.forget_checks()
    yield CODEX
    work_engines.forget_checks()


def test_codex_accepts_every_option_apex_uses(real_codex):
    args, needed = work_plans_check.FLAGS['chatgpt']
    text = subprocess.run([real_codex, *args], capture_output=True, text=True, timeout=60).stdout
    assert [f for f in needed if f not in text] == []


def test_codex_signed_out_and_api_key_sign_ins_are_refused(real_codex, tmp_path):
    got = work_engines.check('chatgpt', fresh=True)
    assert got == {'ok': False, 'why': 'is not signed in', 'how': work_engines.HOW['chatgpt']}
    subprocess.run([real_codex, 'login', '--with-api-key'], input='sk-dummy-not-a-real-key-0000000000',
                   capture_output=True, text=True, timeout=60, env={**os.environ})
    got = work_engines.check('chatgpt', fresh=True)
    assert got['ok'] is False and 'API key' in got['why'] and 'sk-' not in got['why']


def test_claude_accepts_every_option_apex_uses():
    if not CLAUDE:
        pytest.skip('Claude Code is not installed here')
    args, needed = work_plans_check.FLAGS['claude']
    text = subprocess.run([CLAUDE, *args], capture_output=True, text=True, timeout=60).stdout
    assert [f for f in needed if f not in text] == []
    status = subprocess.run([CLAUDE, 'auth', 'status', '--help'], capture_output=True, text=True, timeout=60).stdout
    assert '--json' in status
