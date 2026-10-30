"""A .env saved by Windows tools in another encoding must still load: one line
added with PowerShell 5's `>>` made Apex refuse to start."""
import os

import pytest

import env_file

LINES = 'DASHBOARD_PORT=8123\nCELINE_WAKE_ENABLED=true\nVOICEBOX_PROFILE=Céline\n'


@pytest.mark.parametrize('encoding, data', [
    ('utf-8', LINES.encode('utf-8')),
    ('utf-8 with BOM', b'\xef\xbb\xbf' + LINES.encode('utf-8')),
    ('utf-16 (PowerShell 5 >>)', LINES.encode('utf-16')),
    ('utf-16 big-endian', b'\xfe\xff' + LINES.encode('utf-16-be')),
    ('ANSI / cp1252', LINES.encode('cp1252')),
])
def test_every_windows_encoding_reads_the_same(tmp_path, encoding, data):
    path = tmp_path / '.env'
    path.write_bytes(data)
    values = env_file.env_values(path)
    assert values == {'DASHBOARD_PORT': '8123', 'CELINE_WAKE_ENABLED': 'true', 'VOICEBOX_PROFILE': 'Céline'}, encoding


def test_a_mixed_file_from_powershell_appending_to_utf8_still_loads(tmp_path):
    """`Add-Content` on Windows PowerShell 5 can leave UTF-8 lines followed by UTF-16 ones."""
    path = tmp_path / '.env'
    path.write_bytes('DASHBOARD_PORT=8123\n'.encode('utf-8') + 'CELINE_WAKE_ENABLED=true\n'.encode('utf-16-le'))
    values = env_file.env_values(path)
    assert values == {'DASHBOARD_PORT': '8123', 'CELINE_WAKE_ENABLED': 'true'}, 'the appended setting is recovered'


def test_the_owner_is_told_once_which_encoding_was_used(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(env_file, '_noted', set())
    path = tmp_path / '.env'
    path.write_bytes(LINES.encode('utf-16'))
    env_file.env_text(path)
    env_file.env_text(path)
    out = capsys.readouterr().out
    assert out.count('saved as UTF-16') == 1 and 'Save it as UTF-8' in out
    path.write_bytes(LINES.encode('utf-8'))
    monkeypatch.setattr(env_file, '_noted', set())
    env_file.env_text(path)
    assert capsys.readouterr().out == '', 'nothing to say about a normal file'


def test_a_missing_env_is_empty_not_an_error(tmp_path):
    assert env_file.env_values(tmp_path / '.env') == {}


def test_load_env_sets_the_environment_without_overriding_it(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_bytes('APEX_TEST_ENV_A=from-file\nAPEX_TEST_ENV_B=from-file\n'.encode('utf-16'))
    monkeypatch.delenv('APEX_TEST_ENV_A', raising=False)
    monkeypatch.setenv('APEX_TEST_ENV_B', 'from-environment')
    env_file.load_env(path)
    assert os.environ['APEX_TEST_ENV_A'] == 'from-file'
    assert os.environ['APEX_TEST_ENV_B'] == 'from-environment', 'the environment still wins'
    monkeypatch.delenv('APEX_TEST_ENV_A', raising=False)


def test_the_launchers_read_a_utf16_env(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from scripts import start_screen_companion as startup
    spec = importlib.util.spec_from_file_location('apex_launcher_utf16',
        Path(__file__).resolve().parents[1] / 'scripts/run_apex_qwen.py')
    qwen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qwen)
    (tmp_path / '.env').write_bytes('DASHBOARD_PORT=8123\nCELINE_WAKE_ENABLED=true\n'.encode('utf-16'))
    monkeypatch.setattr(qwen, 'ROOT', tmp_path)
    monkeypatch.setattr(startup, 'ROOT', tmp_path)
    monkeypatch.delenv('DASHBOARD_PORT', raising=False)
    assert qwen.launch_environment()['DASHBOARD_PORT'] == '8123'
    assert startup.environment()['DASHBOARD_PORT'] == '8123'
    from agent import apocalypse
    assert apocalypse.launch_environment(tmp_path, {})['CELINE_WAKE_ENABLED'] == 'true'
