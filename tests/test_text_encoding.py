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
        if name == 'read_text' and not helper and 'encoding' not in kw:
            yield node.lineno, 'read_text'
        if name == 'write_text' and 'encoding' not in kw and len(node.args) < 2:
            yield node.lineno, 'write_text'
        if name == 'open' and isinstance(f, ast.Name) and 'encoding' not in kw:
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
            mode = mode.value if isinstance(mode, ast.Constant) else 'r'
            if 'b' not in str(mode):
                yield node.lineno, 'open'


def test_no_apex_code_reads_or_writes_text_in_the_windows_default_encoding():
    files = [p for d in ('agent', 'app', 'dashboard', 'voice', 'tools') for p in (ROOT / d).rglob('*.py')]
    files += [ROOT / n for n in ('main.py', 'config.py', 'env_file.py', 'text_file.py',
                                 'scripts/run_apex_qwen.py', 'scripts/run_apex_platform.py',
                                 'scripts/run_apex_apocalypse.py', 'scripts/prepare_apocalypse_library.py',
                                 'scripts/setup_apex_apocalypse.py')]
    found = [f'{p.relative_to(ROOT)}:{line} {what}' for p in files for line, what in _unencoded_text_calls(p)]
    assert not found, 'say encoding="utf-8" (or use text_file.read_text): ' + ', '.join(found)
