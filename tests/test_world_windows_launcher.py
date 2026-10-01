"""Exercise the CMD launcher's actual Python bootstrap against stray .env files."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('selected', ['sibling', 'local_bom', 'missing', 'inherited'])
def test_launcher_ignores_broken_parent_dotenv(tmp_path, selected):
    original = tmp_path / 'Apex'
    worktree = tmp_path / 'Apex-world-test'
    original.mkdir()
    worktree.mkdir()
    # This is outside either Apex checkout and must never be used by the test.
    (tmp_path / '.env').write_text('WORLD_LAUNCH_TEST=bad\0value\n', encoding='utf-8')
    env_file = worktree / '.env' if selected == 'local_bom' else original / '.env'
    if selected != 'missing':
        env_file.write_text('WORLD_LAUNCH_TEST=selected\n',
                            encoding='utf-8-sig' if selected == 'local_bom' else 'utf-8')
    expected = 'inherited' if selected == 'inherited' else None if selected == 'missing' else 'selected'
    # Both application entry points call the real dotenv loader again.
    (worktree / 'config.py').write_text('from dotenv import load_dotenv\nload_dotenv()\n')
    (worktree / 'main.py').write_text(
        'import os,sys\nfrom dotenv import load_dotenv\nload_dotenv()\nimport config\n'
        f'assert os.getenv("WORLD_LAUNCH_TEST") == {expected!r}\n'
        'assert sys.argv == ["main.py", "--text"]\n')
    env = os.environ.copy()
    env.pop('WORLD_LAUNCH_TEST', None)
    env.pop('PYTHON_DOTENV_DISABLED', None)
    env['APEX_TEST_ENV_FILE'] = str(env_file)
    if selected == 'inherited':
        env['WORLD_LAUNCH_TEST'] = 'inherited'
    script = (ROOT / 'scripts/test_world_view_windows.cmd').read_text()
    line = next(line for line in script.splitlines() if ' -c "' in line)
    bootstrap = line.split(' -c "', 1)[1][:-1]
    result = subprocess.run([sys.executable, '-c', bootstrap], cwd=worktree,
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert (tmp_path / '.env').read_bytes() == b'WORLD_LAUNCH_TEST=bad\0value\n'


def test_previous_bootstrap_reproduces_reported_failure(tmp_path):
    original = tmp_path / 'Apex'
    worktree = tmp_path / 'Apex-world-test'
    original.mkdir()
    worktree.mkdir()
    (original / '.env').write_text('WORLD_LAUNCH_TEST=selected\n')
    (tmp_path / '.env').write_text('BROKEN_PARENT=bad\0value\n')
    (worktree / 'main.py').write_text('from dotenv import load_dotenv\nload_dotenv()\n')
    env = os.environ.copy()
    env.pop('PYTHON_DOTENV_DISABLED', None)
    env.pop('BROKEN_PARENT', None)
    code = "from dotenv import load_dotenv; load_dotenv('../Apex/.env'); import runpy; runpy.run_path('main.py',run_name='__main__')"
    result = subprocess.run([sys.executable, '-c', code], cwd=worktree, env=env,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0
    assert 'ValueError: embedded null' in result.stderr
