"""The study library: every subject in data/assemblies is discoverable, its
geometry matches its component list, the generated models rebuild
byte-for-byte, and the page, API and companion all see the same subjects."""
import gzip
import importlib
import json
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from agent import assembly

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ('jet-engine', 'heart', 'car-engine')


@pytest.fixture(autouse=True)
def clean_sessions():
    assembly._SESSIONS.clear()
    yield
    assembly._SESSIONS.clear()


def _glb(name):
    raw = gzip.decompress((ROOT / 'dashboard/static/models' / name).read_bytes())
    assert raw[:4] == b'glTF'
    length = struct.unpack_from('<I', raw, 12)[0]
    return raw, json.loads(raw[20:20 + length])


def test_library_lists_every_subject_with_what_the_picker_needs():
    lib = {m['id']: m for m in assembly.library()}
    assert set(GENERATED) | {'dc-motor', 'openmotor-125'} <= set(lib)
    assert [m['id'] for m in assembly.library()][:3] == list(GENERATED), 'the new subjects come first'
    for m in lib.values():
        assert m['title'] and m['fidelity'] and m['parts'] > 0 and m['category']
    assert lib['jet-engine']['motion'] and lib['dc-motor']['motion']
    assert not lib['heart']['motion'] and not lib['openmotor-125']['motion']


@pytest.mark.parametrize('bad', ['../secrets', 'Heart', 'heart.json', '', 'a/b', 'x' * 80, None, 'missing-subject'])
def test_unknown_or_unsafe_ids_are_refused(bad):
    with pytest.raises(ValueError, match='Unknown'):
        assembly.model_path(bad)


@pytest.mark.parametrize('model_id', GENERATED)
def test_generated_subject_geometry_matches_its_component_list(model_id):
    m = assembly.model(model_id)
    assert m['asset'] == f'/api/study/model/{model_id}/asset' and m['asset_file'] == f'{model_id}.glb.gz'
    ids = [p['id'] for p in m['parts']]
    assert len(ids) == len(set(ids)) >= 15
    sources = {s['id'] for s in m['sources']}
    _, doc = _glb(m['asset_file'])
    for p in m['parts']:
        node = doc['nodes'][p['node']]
        assert node['name'] == p['name'] and 'mesh' in node, p['id']
        assert p['source'] in sources and p['purpose'] and p['connection'] and p['model_note']
        assert len(p['explode']) == 3 and all(isinstance(v, float) for v in p['explode'])
    # Honest labels: generated, not to scale, AI-drafted, reviewed by nobody yet.
    assert 'not to scale' in m['fidelity'] and 'AI-drafted' in m['caption']
    assert 'not yet reviewed' in m['limitations'] and 'pending' in m['validation']['review']
    for part, speed in (m.get('motion') or {}).get('parts', {}).items():
        assert part in ids and speed > 0


def test_generated_models_rebuild_byte_for_byte():
    """The committed files are exactly what scripts/build_study_models.py makes."""
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        build = importlib.import_module('build_study_models')
        for spec in build.SUBJECTS.values():
            fresh = build.render(spec)[2]
            committed, _ = _glb(f"{spec['id']}.glb.gz")
            assert fresh == committed, f"{spec['id']}: run scripts/build_study_models.py and bump geometry_revision"
    finally:
        sys.path.remove(str(ROOT / 'scripts'))


def test_motion_only_where_a_subject_has_it():
    sid = assembly.create('jet-engine')['session_id']
    assert assembly.apply(sid, 'rotate')['rotating'] is True
    sid = assembly.create('heart')['session_id']
    with pytest.raises(ValueError, match='only available'):
        assembly.apply(sid, 'rotate')


def test_api_serves_the_library_and_each_subjects_geometry(monkeypatch):
    from dashboard import server
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'library-test')
    with TestClient(server.app) as c:
        assert c.get('/api/study/models').status_code == 401
        assert c.get('/api/study/model/heart/asset').status_code == 401
        c.headers['Authorization'] = 'Bearer library-test'
        ids = [m['id'] for m in c.get('/api/study/models').json()['models']]
        assert set(GENERATED) <= set(ids)
        for model_id, name in [(m, f'{m}.glb.gz') for m in GENERATED] + [('openmotor-125', 'openmotor.glb.gz')]:
            r = c.get(f'/api/study/model/{model_id}/asset')
            assert r.status_code == 200 and r.headers['content-encoding'] == 'gzip', model_id
            assert r.content == _glb(name)[0], f'{model_id} must get its own geometry'
        assert c.get('/api/study/model/dc-motor/asset').status_code == 404      # built in the page, no file
        assert c.get('/api/study/model/nope/asset').status_code == 404
        assert c.get('/api/study/model/..%2Fsecrets/asset').status_code == 404
        sid = c.post('/api/study/session', json={'model': 'car-engine'}).json()['session_id']
        assert c.post('/api/study/session/' + sid, json={'action': 'select', 'part': 'Crankshaft'}).json()['selected'] == 'crankshaft'


def test_companion_can_open_any_subject_and_nothing_else():
    from agent import core
    tool = next(t for t in core.TOOLS if t['name'] == 'assembly_study')
    assert set(GENERATED) <= set(tool['input_schema']['properties']['model']['enum'])
    assert 'heart = Human heart' in tool['description'] and 'say so' in tool['description']
    out = json.loads(core._execute_tool('assembly_study', {'action': 'open', 'model': 'heart'}))
    assert out['url'].startswith('/study?session=') and 'not to scale' in out['notice']
    assert 'not applied' in core._execute_tool('assembly_study', {'action': 'open', 'model': 'toaster'})
