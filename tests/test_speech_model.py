"""The browser speech detector for hands-free voice.

scripts/fetch_speech_model.py installs pinned npm files only when their hash
matches; the dashboard offers the model only when every installed file still
matches its manifest; and voice timing keeps which detector heard each
hands-free turn, so the change is measured, not felt.
"""
import asyncio
import base64
import hashlib
import io
import json
import mimetypes
import tarfile

import pytest

from agent import voice_timing
from scripts import fetch_speech_model as fsm


def tarball(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def fake_packages(monkeypatch):
    """Two small packages shaped like the real ones, with their true hashes."""
    blobs, packages = {}, []
    for package in fsm.PACKAGES:
        data = tarball({member: f'{name} contents'.encode() for member, name in package['files'].items()})
        url = 'https://example.test/' + package['name']
        blobs[url] = data
        packages.append(dict(package, url=url,
                             integrity='sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()))
    monkeypatch.setattr(fsm, 'PACKAGES', packages)
    return blobs


def test_pins_are_exact_versions_with_sha512():
    for package in fsm.PACKAGES:
        assert package['integrity'].startswith('sha512-') and package['version'] in package['url']
    assert set(fsm.FILES) == {'bundle.min.js', 'vad.worklet.bundle.min.js', 'silero_vad_v5.onnx',
                              'ort.wasm.min.js', 'ort-wasm-simd-threaded.mjs', 'ort-wasm-simd-threaded.wasm'}


def test_install_writes_only_the_pinned_files_and_a_manifest(tmp_path, fake_packages):
    target = tmp_path / 'speech'
    manifest = fsm.install(target, download=fake_packages.__getitem__)
    assert sorted(p.name for p in target.iterdir()) == sorted(fsm.FILES + ('manifest.json',))
    assert manifest['files']['bundle.min.js'] == hashlib.sha256(b'bundle.min.js contents').hexdigest()
    assert fsm.status(target) == dict(installed=True, version=fsm.VERSION, problem=None)


def test_a_download_that_does_not_match_its_pin_installs_nothing(tmp_path, fake_packages):
    target = tmp_path / 'speech'
    tampered = dict(fake_packages)
    first = next(iter(tampered))
    tampered[first] = tampered[first] + b'x'
    with pytest.raises(fsm.FetchError, match='pinned hash'):
        fsm.install(target, download=tampered.__getitem__)
    assert not target.exists()


def test_a_changed_or_missing_file_is_not_offered(tmp_path, fake_packages):
    target = tmp_path / 'speech'
    assert fsm.status(target)['problem'].startswith('Not installed')
    fsm.install(target, download=fake_packages.__getitem__)
    (target / 'silero_vad_v5.onnx').write_bytes(b'something else')
    state = fsm.status(target)
    assert not state['installed'] and 'silero_vad_v5.onnx' in state['problem']
    (target / 'silero_vad_v5.onnx').unlink()
    assert not fsm.status(target)['installed']


def test_endpoint_reports_status_and_notices_changes(tmp_path, fake_packages, monkeypatch):
    from dashboard import companion
    target = tmp_path / 'speech'
    monkeypatch.setattr(fsm, 'TARGET', target)
    companion._speech_model_seen.clear()
    assert asyncio.run(companion.speech_model_status())['installed'] is False
    fsm.install(target, download=fake_packages.__getitem__)
    assert asyncio.run(companion.speech_model_status())['installed'] is True
    (target / 'bundle.min.js').write_bytes(b'edited after install!')   # new size: the cache must not hide it
    assert asyncio.run(companion.speech_model_status())['installed'] is False


def test_server_serves_webassembly_with_its_own_type():
    import dashboard.server  # noqa: F401 - pins the types at import
    assert mimetypes.guess_type('x.wasm')[0] == 'application/wasm'
    assert mimetypes.guess_type('x.mjs')[0] == 'text/javascript'


def test_static_vendor_folder_is_ignored_by_git():
    from pathlib import Path
    ignore = (Path(fsm.__file__).resolve().parents[1] / '.gitignore').read_text()
    assert 'dashboard/static/vendor/speech/' in ignore


def test_voice_timing_keeps_the_detector_for_hands_free_turns(test_db):
    def turn(mode, detector, stt):
        voice_timing.record({'mode': mode, 'detector': detector, 'stages': {'stt_done': stt, 'first_sound': stt + 900}})
    turn('hands_free', 'loudness', 1900)
    turn('hands_free', 'model', 1300)
    turn('tap', 'model', 400)                    # a tap turn has no detector
    turn('hands_free', 'telepathy', 1000)        # unknown values are dropped, not stored
    detectors = [t['detector'] for t in voice_timing.recent(10)]
    assert detectors == [None, None, 'model', 'loudness']
    assert voice_timing.summary(detector='model')['stages']['stt_done']['median'] == 1300
    report = voice_timing.report()
    assert 'loudness / speech model' in report and 'Transcript back earlier by 0.60s' in report
