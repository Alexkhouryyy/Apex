"""The hardware check: never crashes, reports only what it observed, keeps a
comparable report. The checks themselves need real devices; here they are faked."""
import json
from types import SimpleNamespace

import pytest

from tools import hardware_check as hc


def entry(fn, key='x', title='X', demo_only=False):
    return {'order': 1, 'key': key, 'title': title, 'fn': fn, 'demo_only': demo_only}


def test_a_crashing_check_becomes_its_own_failure():
    def boom(ctx):
        raise RuntimeError('driver exploded')
    state, detail, m = hc.run_check(entry(boom), hc.Ctx())
    assert state == hc.BAD and 'driver exploded' in detail and m == {}


def test_a_missing_package_is_a_skip_not_a_failure():
    def needs(ctx):
        import not_a_real_module_xyz  # noqa: F401
    assert hc.run_check(entry(needs), hc.Ctx())[0] == hc.SKIP


def test_a_hanging_device_cannot_hang_the_check():
    import threading
    gate = threading.Event()
    state, detail, _ = hc.run_check(entry(lambda ctx: gate.wait(10)), hc.Ctx(), timeout=0.3)
    gate.set()
    assert state == hc.BAD and 'took longer than' in detail


def test_quick_mode_skips_the_hands_on_checks_and_saves_a_report(monkeypatch, tmp_path, capsys):
    ran = []
    fake = [entry(lambda ctx: ran.append('quick') or (hc.OK, 'fine', {'n': 1}), key='quick', title='Quick'),
            entry(lambda ctx: ran.append('demo') or (hc.OK, 'fine', {}), key='demo', title='Demo', demo_only=True)]
    monkeypatch.setattr(hc, 'CHECKS', fake)
    assert hc.main([], out_dir=tmp_path) == 0
    assert ran == ['quick'], 'no camera, microphone or speaker demo unless --demo'
    saved = list(tmp_path.glob('*.json'))
    assert len(saved) == 1 and json.loads(saved[0].read_text())['results'][0]['state'] == 'ok'
    assert list(tmp_path.glob('*.txt'))
    assert hc.main(['--demo'], out_dir=tmp_path) == 0 and ran[-1] == 'demo'


def test_the_report_says_what_changed_since_last_time(monkeypatch, tmp_path, capsys):
    values = iter([30.0, 12.5])
    state = iter([hc.OK, hc.WARN])
    monkeypatch.setattr(hc, 'CHECKS', [entry(lambda ctx: (next(state), 'cam', {'camera_fps': next(values)}),
                                             key='camera', title='Webcam')])
    import time
    hc.main([], out_dir=tmp_path)
    time.sleep(1.1)                                   # reports are named by the second
    capsys.readouterr()
    assert hc.main([], out_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert 'camera_fps: 30.0 -> 12.5' in out and 'Webcam: ok -> warn' in out


def test_a_failure_sets_the_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, 'CHECKS', [entry(lambda ctx: (hc.BAD, 'no frames', {}))])
    assert hc.main([], out_dir=tmp_path) == 1


def test_low_memory_warns_about_the_paging_file_error(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(total=8e9, available=2e9))
    monkeypatch.setattr(psutil, 'swap_memory', lambda: SimpleNamespace(total=2e9, free=1e9))
    state, detail, m = hc._memory(hc.Ctx())
    assert state == hc.WARN and '1455' in detail and m['ram_free_gb'] == 2.0


def test_the_gpu_check_reads_free_memory(monkeypatch):
    monkeypatch.setattr(hc.shutil, 'which', lambda name: '/usr/bin/nvidia-smi')
    monkeypatch.setattr(hc.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='NVIDIA GeForce RTX 3060 Laptop GPU, 6144, 3000, 560.94\n', stderr=''))
    state, detail, m = hc._gpu(hc.Ctx())
    assert state == hc.WARN and m['gpu_free_mb'] == 3144 and 'Celine' in detail
    monkeypatch.setattr(hc.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='NVIDIA GeForce RTX 3060 Laptop GPU, 6144, 500, 560.94\n', stderr=''))
    assert hc._gpu(hc.Ctx())[0] == hc.OK


def test_a_silent_microphone_is_a_failure(monkeypatch):
    import sys
    import numpy as np
    fake = SimpleNamespace(query_devices=lambda kind: {'name': 'Mic', 'default_samplerate': 16000},
                           rec=lambda frames, **k: np.zeros((frames, 1), dtype='float32'), wait=lambda: None)
    monkeypatch.setitem(sys.modules, 'sounddevice', fake)
    state, detail, _ = hc._microphone(hc.Ctx(say=lambda t: None))
    assert state == hc.BAD and 'muted' in detail


def test_the_speaker_check_asks_whether_the_tone_was_heard(monkeypatch):
    import sys
    fake = SimpleNamespace(query_devices=lambda kind: {'name': 'Speakers', 'default_samplerate': 44100},
                           play=lambda *a, **k: None, wait=lambda: None)
    monkeypatch.setitem(sys.modules, 'sounddevice', fake)
    assert hc._speakers(hc.Ctx(ask=lambda q: 'n'))[0] == hc.BAD
    assert hc._speakers(hc.Ctx(ask=lambda q: 'yes'))[0] == hc.OK


def test_the_one_click_launcher_runs_the_demo():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / 'Test-Apex-Hardware.cmd').read_bytes()
    assert b'\r\n' in text and b'\n' not in text.replace(b'\r\n', b''), 'CRLF, or cmd.exe misreads it'
    assert b'-m tools.hardware_check' in text and b'--demo' in text
