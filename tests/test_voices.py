"""Custom voices: the folder library, the voice servers speaking any voice in
it, the recording checks, and the Voices page's API.

The GPU engines are fakes; what is tested is which voice is used, what is
refused, and that a bad recording is caught before it becomes a bad clone.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from scripts import voice_library
from voice import voice_check

ROOT = Path(__file__).resolve().parents[1]
RATE = voice_check.RATE
SCRIPT = "Hey, it's Alex. Today was a good day and I learned something new."


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def speech(seconds=20.0, level=0.3, noise=0.001, seed=1):
    """Something with the shape of speech: voiced syllables with short pauses."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    voiced = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 700), 1))
    syllables = (np.sin(2 * np.pi * 2.5 * t) > -0.2).astype(float)
    out = level * voiced / np.abs(voiced).max() * syllables + noise * rng.standard_normal(len(t))
    return out.astype(np.float32)


def voice_folder(root, vid, transcript=SCRIPT, name=None):
    folder = root / vid
    folder.mkdir(parents=True)
    (folder / 'reference.wav').write_bytes(voice_check.to_wav(speech(2)))
    (folder / 'transcript.txt').write_text(transcript)
    if name:
        (folder / 'voice.json').write_text(json.dumps({'name': name}))
    return folder


# --- the library ------------------------------------------------------------------------

def test_library_finds_folder_voices_and_keeps_celine_first(tmp_path):
    legacy = tmp_path / 'celine.ogg'
    legacy.write_bytes(b'x')
    voice_folder(tmp_path / 'v', 'alex', name='Alex')
    voice_folder(tmp_path / 'v', 'bea')
    (tmp_path / 'v' / 'broken').mkdir()                                   # no recording: skipped
    voice_folder(tmp_path / 'v', 'Bad_Name')                              # not a valid id: skipped
    found = voice_library.discover(tmp_path / 'v', legacy=legacy, legacy_transcript='Hello Alex.')
    assert [v.id for v in found] == ['celine', 'alex', 'bea']
    assert found[0].folder is False and found[1].name == 'Alex' and found[2].name == 'BEA'
    # A celine folder replaces the original recording.
    voice_folder(tmp_path / 'v', 'celine', name='Celine')
    found = voice_library.discover(tmp_path / 'v', legacy=legacy, legacy_transcript='Hello Alex.')
    assert found[0].id == 'celine' and found[0].folder is True


def test_choose_by_id_or_name_and_say_what_exists(tmp_path):
    voice_folder(tmp_path, 'alex', name='Alex')
    voice_folder(tmp_path, 'celine', name='Celine')
    found = voice_library.discover(tmp_path)
    assert voice_library.choose(found).id == 'celine'
    assert voice_library.choose(found, 'ALEX').id == 'alex' and voice_library.choose(found, 'alex').id == 'alex'
    with pytest.raises(ValueError, match='Voices: Celine, Alex'):
        voice_library.choose(found, 'ryan')
    with pytest.raises(ValueError, match='No voices yet'):
        voice_library.choose([])
    assert voice_library.slug('Céline 2!') == 'celine-2' and voice_library.slug('  Alex ') == 'alex'


def test_rerecording_changes_the_stamp(tmp_path):
    folder = voice_folder(tmp_path, 'alex')
    before = voice_library.discover(tmp_path)[0].stamp
    os.utime(folder / 'transcript.txt', (before + 10, before + 10))
    assert voice_library.discover(tmp_path)[0].stamp > before


# --- the voice servers --------------------------------------------------------------------

class FakeStream:
    sample_rate = 24000

    def __init__(self):
        self.used = []

    def stream(self, text, voice):
        self.used.append(voice.id)
        yield np.zeros(240, dtype=np.float32)


class FakeWav:
    def __init__(self):
        self.used = []

    def generate(self, text, voice):
        self.used.append(voice.id)
        return voice_check.to_wav(np.zeros(240, dtype=np.float32))


@pytest.mark.parametrize('module,engine,route', [('qwen_fast_server', FakeStream, '/generate/pcm'),
                                                 ('qwen_fast_server', FakeStream, '/generate/stream'),
                                                 ('qwen_server', FakeWav, '/generate/stream')])
def test_servers_speak_the_chosen_voice(tmp_path, module, engine, route):
    voice_folder(tmp_path, 'alex', name='Alex')
    voice_folder(tmp_path, 'celine', name='Celine')
    fake = engine()
    app = _load(module).create_app(fake, lambda: voice_library.discover(tmp_path))
    with TestClient(app, base_url='http://127.0.0.1') as c:
        assert [p['name'] for p in c.get('/profiles').json()] == ['Celine', 'Alex']
        assert c.get('/health').json()['voices'] == ['celine', 'alex']
        assert c.post(route, json={'text': 'Hi', 'profile_id': 'alex', 'engine': 'qwen'}).status_code == 200
        assert c.post(route, json={'text': 'Hi'}).status_code == 200                     # default: Celine
        r = c.post(route, json={'text': 'Hi', 'profile_id': 'ryan', 'engine': 'qwen'})
        assert r.status_code == 400 and 'Celine, Alex' in r.json()['detail']
        # A voice saved while the server runs is usable at once.
        voice_folder(tmp_path, 'bea', name='Bea')
        assert c.post(route, json={'text': 'Hi', 'profile_id': 'bea', 'engine': 'qwen'}).status_code == 200
    assert fake.used == ['alex', 'celine', 'bea']


# --- recording checks ------------------------------------------------------------------------

def test_a_clean_recording_passes():
    r = voice_check.analyse(speech(), SCRIPT, heard=SCRIPT)
    assert not r['problems'] and not r['warnings'] and r['match'] == 1.0


@pytest.mark.parametrize('audio,needle', [
    (speech(3), 'Too short'),
    (np.clip(speech(level=2.0), -1, 1), 'distorting'),
    (speech(level=0.005, noise=0.00001), 'Too quiet'),
    (speech(noise=0.12), 'background noise'),
])
def test_bad_recordings_are_caught_with_a_plain_reason(audio, needle):
    problems = voice_check.analyse(audio, SCRIPT, heard=SCRIPT)['problems']
    assert any(needle in p for p in problems), problems


def test_words_must_match_the_transcript():
    r = voice_check.analyse(speech(), SCRIPT, heard='Completely different words about the weather in Paris.')
    assert any("don't match" in p for p in r['problems'])
    r = voice_check.analyse(speech(), SCRIPT, heard="Hey it's Alex. Today was a great day and I learned a lot.")
    assert not r['problems'] and any('few words differ' in w for w in r['warnings'])
    assert voice_check.match("Hey, it's Alex!", "hey its alex") > 0.6


def test_trim_cuts_silence_but_keeps_the_words():
    padded = np.concatenate([np.zeros(3 * RATE, np.float32), speech(10), np.zeros(4 * RATE, np.float32)])
    trimmed = voice_check.trim(padded)
    assert 10 <= len(trimmed) / RATE < 11


def test_decode_reads_wav_and_refuses_junk():
    audio = voice_check.decode(voice_check.to_wav(speech(2)))
    assert abs(len(audio) / RATE - 2) < 0.05
    with pytest.raises(ValueError, match='not audio'):
        voice_check.decode(b'not a recording at all' * 20)


# --- the Voices page API ------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('APEX_VOICES_DIR', str(tmp_path / 'voices'))
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'voices-test')
    from dashboard import server, voices
    from dashboard.ratelimit import AuthThrottle
    monkeypatch.setattr(server, '_throttle', AuthThrottle())     # earlier tests' failed logins must not lock this one out
    heard = {'text': SCRIPT}
    monkeypatch.setattr(voices, '_listen', lambda wav: heard['text'])
    monkeypatch.setattr(voices, '_legacy', lambda: dict(legacy=tmp_path / 'missing.ogg', legacy_transcript='x'))
    with TestClient(server.app) as c:
        c.heard = heard
        c.root = tmp_path / 'voices'
        yield c


def body(**extra):
    return dict(audio=base64.b64encode(voice_check.to_wav(speech())).decode(), transcript=SCRIPT, **extra)


def test_voices_need_the_owner(api):
    assert api.get('/api/voices').status_code == 401
    assert api.post('/api/voices', json=body(name='Alex', consent=True)).status_code == 401


def test_record_check_save_replace_remove(api):
    api.headers['Authorization'] = 'Bearer voices-test'
    report = api.post('/api/voices/check', json=body()).json()
    assert report['problems'] == [] and report['match'] == 1.0
    assert api.post('/api/voices', json=body(name='Alex')).status_code == 400              # no consent
    r = api.post('/api/voices', json=body(name='Alex', consent=True))
    assert r.status_code == 200 and r.json()['id'] == 'alex'
    folder = api.root / 'alex'
    assert (folder / 'transcript.txt').read_text().strip() == SCRIPT
    assert json.loads((folder / 'voice.json').read_text())['name'] == 'Alex'
    assert [v['name'] for v in api.get('/api/voices').json()['voices']] == ['Alex']
    # The servers see exactly what was saved.
    assert voice_library.discover(api.root)[0].transcript == SCRIPT
    assert api.post('/api/voices', json=body(name='alex', consent=True)).status_code == 409
    assert api.post('/api/voices', json=body(name='Alex', consent=True, replace=True)).status_code == 200
    assert len(list((api.root / '.trash').iterdir())) == 1
    # Words that don't match are refused, with what Apex heard.
    api.heard['text'] = 'Something else entirely about the football match last night.'
    r = api.post('/api/voices', json=body(name='Bea', consent=True))
    assert r.status_code == 422 and "don't match" in r.json()['detail']
    assert api.delete('/api/voices/alex').status_code == 200
    assert api.get('/api/voices').json()['voices'] == [] and len(list((api.root / '.trash').iterdir())) == 2
    assert api.delete('/api/voices/celine').status_code == 404


def test_a_per_device_token_cannot_make_voices(api, monkeypatch):
    from agent import access_tokens
    monkeypatch.setattr(access_tokens, 'verify', lambda t: t == 'device-token')
    api.headers['Authorization'] = 'Bearer device-token'
    assert api.get('/api/voices').status_code == 200
    r = api.post('/api/voices', json=body(name='Alex', consent=True))
    assert r.status_code == 403 and 'owner' in r.json()['detail']


def test_the_page_shell_loads_before_login(api):
    r = api.get('/voices')
    assert r.status_code == 200 and 'id="record"' in r.text
