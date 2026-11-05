"""Apex's text files read the same whatever launcher wrote them.

On the owner's laptop, Start-Apex-Platform stopped both Apex and Apocalypse with
"'charmap' codec can't decode byte 0x9d in position 1749": one launch (UTF-8
mode) had saved Celine's memory file with a curly quote as UTF-8, and the
platform's children (Windows' default cp1252) could not read it back.
"""
import ast
from pathlib import Path

import pytest

import text_file

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('raw, text', [
    ('Alex said “hi”'.encode('utf-8'), 'Alex said “hi”'),            # the laptop's crash: E2 80 9D
    ('Alex — it’s fine'.encode('cp1252'), 'Alex — it’s fine'),        # files saved the old way
    (b'\xef\xbb\xbfBOM first', 'BOM first'),
    (b'', ''),
])
def test_text_is_read_in_either_encoding(tmp_path, raw, text):
    path = tmp_path / 'f.md'
    path.write_bytes(raw)
    assert text_file.read_text(path) == text


def test_a_byte_limit_never_splits_a_character_or_falls_back(tmp_path):
    path = tmp_path / 'f.md'
    path.write_bytes('ab”cd'.encode('utf-8'))                # ” is 3 bytes: 2..4
    assert text_file.read_text(path, limit=3) == 'ab'        # cut inside ”: dropped, still UTF-8
    assert text_file.read_text(path, limit=5) == 'ab”'
    assert text_file.read_text(path, limit=100) == 'ab”cd'
    path.write_bytes('a’b'.encode('cp1252'))
    assert text_file.read_text(path, limit=2) == 'a’'


def test_celine_s_memory_saved_either_way_loads_and_is_saved_as_utf8(tmp_path, monkeypatch):
    from agent import longterm
    monkeypatch.setattr(longterm, '_APEX_MEMORY_DIR', tmp_path)
    monkeypatch.setattr(longterm, '_MEMORY_FILE', tmp_path / 'APEX_MEMORY.md')
    monkeypatch.setattr(longterm, '_USER_FILE', tmp_path / 'APEX_USER.md')
    (tmp_path / 'APEX_MEMORY.md').write_bytes('Owner’s drive is D: — keep it'.encode('cp1252'))
    (tmp_path / 'APEX_USER.md').write_bytes('Calls her “Celly”'.encode('utf-8'))
    loaded = longterm.load_memory_files()
    assert loaded == {'memory': 'Owner’s drive is D: — keep it', 'user': 'Calls her “Celly”'}
    longterm.save_memory_entry('memory', 'add', 'Prefers “short” answers', _bypass_approval=True)
    raw = (tmp_path / 'APEX_MEMORY.md').read_bytes()
    assert raw.decode('utf-8') == 'Owner’s drive is D: — keep it § Prefers “short” answers'


def test_every_launched_apex_runs_in_utf8_mode(monkeypatch):
    from scripts import run_apex_qwen
    from agent import apocalypse
    monkeypatch.delenv('PYTHONUTF8', raising=False)
    assert run_apex_qwen.launch_environment()['PYTHONUTF8'] == '1'
    assert apocalypse.launch_environment(ROOT, {})['PYTHONUTF8'] == '1'
    platform = (ROOT / 'scripts/run_apex_platform.py').read_text(encoding='utf-8')
    assert "apex_env['PYTHONUTF8'] = offline['PYTHONUTF8'] = '1'" in platform


def _unencoded_text_calls(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f, kw = node.func, {k.arg for k in node.keywords}
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ''
        helper = isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ('_text_file', 'text_file', 'doc_convert')
        method = isinstance(f, ast.Attribute)               # Path.read_text, not a local read_text()
        if name == 'read_text' and method and not helper and 'encoding' not in kw:
            yield node.lineno, 'read_text'
        if name == 'write_text' and method and 'encoding' not in kw and len(node.args) < 2:
            yield node.lineno, 'write_text'
        if name in ('FileHandler', 'RotatingFileHandler', 'TimedRotatingFileHandler') and 'encoding' not in kw:
            yield node.lineno, name                     # a log line with Arabic or an emoji must not fail
        if name == 'open' and isinstance(f, ast.Name) and 'encoding' not in kw:
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
            mode = mode.value if isinstance(mode, ast.Constant) else 'r'
            if 'b' not in str(mode):
                yield node.lineno, 'open'


def test_no_apex_code_reads_or_writes_text_in_the_windows_default_encoding():
    files = [p for d in ('agent', 'app', 'dashboard', 'voice', 'tools', 'scripts') for p in (ROOT / d).rglob('*.py')]
    files += [ROOT / n for n in ('main.py', 'config.py', 'env_file.py', 'text_file.py')]
    found = [f'{p.relative_to(ROOT)}:{line} {what}' for p in files for line, what in _unencoded_text_calls(p)]
    assert not found, 'say encoding="utf-8" (or use text_file.read_text): ' + ', '.join(found)


def test_a_startup_failure_says_where_and_saves_the_details(tmp_path):
    from scripts.run_apex_qwen import describe_failure
    from agent import longterm
    try:                                                  # the laptop's error, raised "in" Apex's own code
        exec(compile('b"\\x9d".decode("cp1252")', str(Path(longterm.__file__)), 'exec'))
    except UnicodeDecodeError as exc:
        line = describe_failure(exc, 'Apocalypse', home=tmp_path)
    assert line.startswith("'charmap' codec can't decode byte 0x9d")
    assert '(at agent/longterm.py:1 in <module>)' in line
    report = tmp_path / '.apex' / 'last-startup-error.txt'
    assert f'Details: {report}' in line
    saved = report.read_text(encoding='utf-8')
    assert saved.startswith('Apocalypse, ') and 'UnicodeDecodeError' in saved and 'Traceback' in saved


def test_frames_outside_apex_and_pseudo_files_are_skipped(tmp_path):
    from scripts.run_apex_qwen import describe_failure
    try:
        exec(compile('raise ValueError("boom")', '<string>', 'exec'))
    except ValueError as exc:
        line = describe_failure(exc, 'Apex launcher', home=tmp_path)
    assert line.startswith('boom (at tests/test_text_encoding.py:') and '<string>' not in line


def test_both_launchers_report_failures_with_the_location():
    for name in ('run_apex_qwen.py', 'run_apex_apocalypse.py'):
        assert 'describe_failure(exc, ' in (ROOT / 'scripts' / name).read_text(encoding='utf-8')


def test_the_supervisor_sign_in_and_autostart_paths_run_in_utf8_mode(monkeypatch):
    from types import SimpleNamespace
    from scripts import apex_supervisor, start_screen_companion
    seen = []
    monkeypatch.setattr(apex_supervisor.subprocess, 'run',
                        lambda cmd, cwd, env: seen.append(env) or SimpleNamespace(returncode=0))
    apex_supervisor.run(['--resident'])
    assert seen[0]['PYTHONUTF8'] == '1'
    assert start_screen_companion.environment()['PYTHONUTF8'] == '1'
    autostart = (ROOT / 'app/autostart.py').read_text(encoding='utf-8')
    assert '''value = f'"{python_exe}" -X utf8 "{_MAIN_PY}" --resident\'''' in autostart


def test_celine_s_voice_server_also_runs_in_utf8_mode(monkeypatch, tmp_path):
    from unittest.mock import Mock
    from scripts import run_apex_qwen as launcher
    monkeypatch.delenv('PYTHONUTF8', raising=False)
    monkeypatch.setattr(launcher, 'port_in_use', lambda *a, **k: False)
    spawn = Mock(return_value=Mock())
    monkeypatch.setattr(launcher.subprocess, 'Popen', spawn)
    python = tmp_path / 'python.exe'
    python.touch()
    launcher.start_voice(python, 'scripts/qwen_fast_server.py', opener=None)
    assert spawn.call_args.kwargs['env']['PYTHONUTF8'] == '1'
