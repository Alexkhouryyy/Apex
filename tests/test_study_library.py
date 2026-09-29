"""The study library: every subject in data/assemblies is discoverable, its
geometry matches its component list, the generated models rebuild apart from
platform roundoff at zero, and the page, API and companion see the same subjects."""
import gzip
import importlib
import json
import struct
import sys
from pathlib import Path

import pytest
import numpy as np
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
    assert lib['jet-engine']['motion'] and lib['dc-motor']['motion'] and lib['heart']['motion'] and lib['car-engine']['motion']
    assert not lib['openmotor-125']['motion']


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


def _assert_same_generated_glb(fresh, committed):
    """Only nominal-zero float roundoff may differ; everything else is exact.

    Windows and Linux math kernels differ in a few near-zero position/normal
    components (observed maximum 5e-15). Regenerating assets on Windows would
    just move the byte-for-byte failure to Linux. Keep metadata, nonzero floats,
    topology and padding exact, with a 1e-14 absolute allowance only near zero.
    """
    assert len(fresh) == len(committed), 'GLB length changed'
    json_length = struct.unpack_from('<I', committed, 12)[0]
    binary_start = 28 + json_length
    assert fresh[:binary_start] == committed[:binary_start], 'GLB metadata changed'
    doc = json.loads(committed[20:20 + json_length])
    normalized = bytearray(fresh)
    for accessor in doc['accessors']:
        if accessor['componentType'] != 5126:
            continue
        view = doc['bufferViews'][accessor['bufferView']]
        assert 'byteStride' not in view  # The generated models use packed arrays.
        offset = binary_start + view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
        count = accessor['count'] * {'VEC3': 3, 'SCALAR': 1}[accessor['type']]
        left = np.frombuffer(fresh, dtype='<f4', count=count, offset=offset)
        right = np.frombuffer(committed, dtype='<f4', count=count, offset=offset)
        different = left != right
        assert np.all(np.isfinite(left)) and np.all(np.isfinite(right)), 'Non-finite geometry'
        assert np.all(np.abs(left[different]) <= 1e-14) and np.all(np.abs(right[different]) <= 1e-14), \
            'Geometry changed beyond nominal-zero roundoff'
        normalized[offset:offset + count * 4] = committed[offset:offset + count * 4]
    assert bytes(normalized) == committed, 'GLB indices or other bytes changed'


def test_generated_models_rebuild_with_only_zero_roundoff():
    """Committed assets match the generator across Windows/Linux math kernels."""
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        build = importlib.import_module('build_study_models')
        for spec in build.SUBJECTS.values():
            fresh = build.render(spec)[2]
            committed, _ = _glb(f"{spec['id']}.glb.gz")
            try:
                _assert_same_generated_glb(fresh, committed)
            except AssertionError as exc:
                raise AssertionError(f"{spec['id']}: {exc}; run scripts/build_study_models.py and bump geometry_revision") from exc
    finally:
        sys.path.remove(str(ROOT / 'scripts'))


def _comparison_fixture():
    doc = {'accessors': [{'componentType': 5126, 'bufferView': 0, 'count': 1, 'type': 'VEC3'},
                         {'componentType': 5125, 'bufferView': 1, 'count': 1, 'type': 'SCALAR'}],
           'bufferViews': [{'byteOffset': 0, 'byteLength': 12}, {'byteOffset': 12, 'byteLength': 4}]}
    js = json.dumps(doc).encode()
    js += b' ' * (-len(js) % 4)
    blob = struct.pack('<fffI', 0.0, 1.0, 2.0, 0)
    return struct.pack('<4sII', b'glTF', 2, 28 + len(js) + len(blob)) + \
        struct.pack('<I4s', len(js), b'JSON') + js + struct.pack('<I4s', len(blob), b'BIN\0') + blob


def test_model_comparison_accepts_only_nominal_zero_roundoff():
    committed = _comparison_fixture()
    fresh = bytearray(committed)
    struct.pack_into('<f', fresh, len(fresh) - 16, -5e-15)
    _assert_same_generated_glb(fresh, committed)


@pytest.mark.parametrize('change', ['vertex', 'nonzero_ulp', 'index', 'metadata', 'nan'])
def test_model_comparison_rejects_real_changes(change):
    committed = _comparison_fixture()
    fresh = bytearray(committed)
    if change == 'vertex':
        struct.pack_into('<f', fresh, len(fresh) - 16, 1e-5)
    elif change == 'nonzero_ulp':
        struct.pack_into('<I', fresh, len(fresh) - 12, 0x3f800001)
    elif change == 'index':
        struct.pack_into('<I', fresh, len(fresh) - 4, 1)
    elif change == 'nan':
        struct.pack_into('<f', fresh, len(fresh) - 16, float('nan'))
    else:
        fresh[8] ^= 1  # Header length is exact, too.
    with pytest.raises(AssertionError):
        _assert_same_generated_glb(fresh, committed)


def test_motion_only_where_a_subject_has_it():
    sid = assembly.create('jet-engine')['session_id']
    assert assembly.apply(sid, 'rotate')['rotating'] is True
    sid = assembly.create('openmotor-125')['session_id']
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


def test_companion_can_open_any_subject_and_nothing_else(test_db, monkeypatch):
    from agent import core, board, board_workspaces
    # Opening a study emits on a persisted board. Never depend on an earlier
    # test having initialized the singleton, or open the owner's real database.
    monkeypatch.setattr(board, '_board', None)
    monkeypatch.setattr(board_workspaces, '_boards', {})
    tool = next(t for t in core.TOOLS if t['name'] == 'assembly_study')
    assert set(GENERATED) <= set(tool['input_schema']['properties']['model']['enum'])
    assert 'heart = Human heart' in tool['description'] and 'say so' in tool['description']
    out = json.loads(core._execute_tool('assembly_study', {'action': 'open', 'model': 'heart'}))
    assert out['url'].startswith('/study?session=') and 'not to scale' in out['notice']
    assert 'not applied' in core._execute_tool('assembly_study', {'action': 'open', 'model': 'toaster'})
