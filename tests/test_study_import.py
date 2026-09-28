"""Importing a real 3D model into the study library: safety checks on the
file, components from its node tree, storage outside the repo, AI-drafted
notes that never change the manifest, and removal."""
import gzip
import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from agent import assembly, study_import as si

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def study_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('APEX_STUDY_DIR', str(tmp_path / 'study'))
    assembly._SESSIONS.clear()
    yield tmp_path / 'study'
    assembly._SESSIONS.clear()


def glb(nodes, scene_roots=(0,), meshes=None, extra=None, binary=True):
    """A tiny valid glTF: every mesh is one triangle."""
    tri = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    count = 1 + max([n['mesh'] for n in nodes if 'mesh' in n] or [0])
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': list(scene_roots)}], 'nodes': nodes,
           'meshes': meshes or [{'name': f'Object_{k}', 'primitives': [{'attributes': {'POSITION': 0}}]} for k in range(count)],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3', 'min': [0, 0, 0], 'max': [1, 1, 0]}],
           'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(tri)}], 'buffers': [{'byteLength': len(tri)}]}
    doc.update(extra or {})
    if not binary:
        import base64
        doc['buffers'][0]['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(tri).decode()
        return json.dumps(doc).encode()
    js = json.dumps(doc).encode(); js += b' ' * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(tri) + 0
    return struct.pack('<4sII', b'glTF', 2, total + (-len(tri) % 4)) + struct.pack('<I4s', len(js), b'JSON') + js + \
        struct.pack('<I4s', len(tri) + (-len(tri) % 4), b'BIN\x00') + tri + b'\0' * (-len(tri) % 4)


def sketchfab_like():
    """The usual export: wrappers, then named objects, each wrapping an Object_N mesh."""
    return glb([
        {'name': 'Sketchfab_model', 'children': [1]},
        {'name': 'root', 'children': [2]},
        {'name': 'GLTF_SceneRootNode', 'children': [3, 5, 7, 9]},
        {'name': 'Front_wheel_0', 'children': [4]}, {'name': 'Object_4', 'mesh': 0},
        {'name': 'Rear_wheel_1', 'children': [6]}, {'name': 'Object_6', 'mesh': 1},
        {'name': 'Frame_2', 'children': [8]}, {'name': 'Object_8', 'mesh': 2},
        {'name': 'Object_9', 'mesh': 3},
    ])


def names(parts):
    return [p['name'] for p in parts]


def test_named_objects_are_found_under_export_wrappers():
    parts = si.components(si.read_gltf(sketchfab_like()))
    assert names(parts) == ['Front wheel', 'Rear wheel', 'Frame', 'Part 1']
    assert [p['nodes'] for p in parts] == [[3], [5], [7], [9]]
    assert len({p['id'] for p in parts}) == 4


def test_generic_names_fall_back_to_mesh_names_then_numbers():
    parts = si.components(si.read_gltf(glb([{'name': 'Scene', 'children': [1, 2]}, {'name': 'Object_1', 'mesh': 0},
                                             {'name': 'node12', 'mesh': 1}],
                                            meshes=[{'name': 'Gear_housing', 'primitives': []}, {'name': 'Mesh.003', 'primitives': []}])))
    assert names(parts) == ['Gear housing', 'Part 1']


def test_many_repeats_merge_and_every_piece_goes_deeper():
    bolts = [{'name': f'Bolt:{k}', 'mesh': 0} for k in range(70)]
    nodes = [{'name': 'Assembly', 'children': [1, 2]}, {'name': 'Base', 'mesh': 0},
             {'name': 'Fasteners', 'children': list(range(3, 73))}] + bolts
    doc = si.read_gltf(glb(nodes))
    assert names(si.components(doc, 'auto')) == ['Base', 'Fasteners']
    fine = si.components(doc, 'fine')
    assert names(fine) == ['Base', 'Bolt ×70'] and len(fine[1]['nodes']) == 70 and fine[1]['group'] == 'Fasteners'


def test_repeated_names_are_numbered_when_few():
    parts = si.components(si.read_gltf(glb([{'name': 'Kit', 'children': [1, 2, 3]}] + [{'name': 'Radiator v28:1', 'mesh': 0}] * 3)))
    assert names(parts) == ['Radiator v28 #1', 'Radiator v28 #2', 'Radiator v28 #3']


def test_the_real_openmotor_cad_file_splits_sensibly():
    raw = gzip.decompress((ROOT / 'dashboard/static/models/openmotor.glb.gz').read_bytes())
    auto = names(si.components(si.read_gltf(raw)))
    assert auto[:4] == ['Stator', 'Rotor', 'Magnet asm', 'Rotor holder'] and len(auto) == 19
    fine = names(si.components(si.read_gltf(raw), 'fine'))
    assert 'Magnet ×28' in fine and 'Tooth ×24' in fine and 'Coil ×24' in fine and len(fine) <= si.MANY_PARTS


@pytest.mark.parametrize('raw, message', [
    (b'hello', 'not a glTF'),
    (b'glTF' + struct.pack('<II', 1, 12), 'glTF 2.0'),
    (b'glTF' + struct.pack('<II', 2, 999) + b'\0' * 12, 'truncated'),
    (json.dumps({'asset': {'version': '1.0'}}).encode(), 'glTF 2.0'),
    (json.dumps({'asset': {'version': '2.0'}, 'nodes': [], 'meshes': []}).encode(), 'no 3D geometry'),
])
def test_unusable_files_get_a_plain_reason(raw, message):
    with pytest.raises(si.StudyImportError, match=message):
        si.read_gltf(raw)


def test_compressed_and_linked_files_are_refused_with_how_to_fix():
    draco = glb([{'name': 'A', 'mesh': 0}], extra={'extensionsRequired': ['KHR_draco_mesh_compression']})
    with pytest.raises(si.StudyImportError, match='Draco.*untick "Compression"'):
        si.read_gltf(draco)
    linked = glb([{'name': 'A', 'mesh': 0}], extra={'images': [{'uri': 'https://example.com/t.png'}]})
    with pytest.raises(si.StudyImportError, match='separate files'):
        si.read_gltf(linked)
    relative = glb([{'name': 'A', 'mesh': 0}], extra={'images': [{'uri': '../../secret.png'}]})
    with pytest.raises(si.StudyImportError, match='separate files'):
        si.read_gltf(relative)
    si.read_gltf(glb([{'name': 'A', 'mesh': 0}], binary=False))       # embedded .gltf is fine


def test_size_limit(monkeypatch):
    monkeypatch.setattr(si, 'MAX_BYTES', 100)
    with pytest.raises(si.StudyImportError, match='larger than'):
        si.read_gltf(sketchfab_like())


def test_import_stores_outside_the_repo_and_joins_the_library(study_dir):
    m = si.import_model(sketchfab_like(), file_name='Bicycle_v2.glb', source_url='https://example.com/bike', license='CC BY 4.0')
    assert m['title'] == 'Bicycle v2' and m['category'] == 'Imported' and m['auto_explode']
    assert (study_dir / f"{m['id']}.json").is_file() and (study_dir / f"{m['id']}.glb.gz").is_file()
    assert gzip.decompress((study_dir / f"{m['id']}.glb.gz").read_bytes()) == sketchfab_like()
    lib = {x['id']: x for x in assembly.library()}
    assert lib[m['id']]['imported'] and lib[m['id']]['parts'] == 4 and not lib['heart']['imported']
    assert assembly.asset_path(m['id']) == study_dir / f"{m['id']}.glb.gz"
    sid = assembly.create(m['id'])['session_id']
    assert assembly.apply(sid, 'select', part='Front wheel')['selected'] == 'front-wheel'
    with pytest.raises(si.StudyImportError, match='http'):
        si.import_model(sketchfab_like(), source_url='javascript:alert(1)')
    single = si.import_model(glb([{'name': 'Teapot', 'mesh': 0}]), file_name='teapot.glb')
    assert 'cannot be taken apart' in single['limitations']


def test_drafted_notes_overlay_without_changing_the_manifest_or_saved_studies():
    from agent import study_projects
    m = si.import_model(sketchfab_like(), file_name='bike.glb')
    before = study_projects.model_hash(m['id'])
    seen = {}

    def fake(system, user):
        seen['user'] = json.loads(user)
        return 'Sure! ```json\n' + json.dumps({'summary': 'A bicycle.', 'parts': {
            'front-wheel': {'purpose': 'Steers and rolls.', 'connection': 'Held by the fork.'},
            'frame': {'purpose': 'The main structure.'},
            'invented-part': {'purpose': 'Should be ignored.'}}}) + '\n```'

    record = si.draft_notes(m, fake, about='a road bike')
    assert set(record['parts']) == {'front-wheel', 'frame'}
    assert seen['user']['what_the_person_said_it_is'] == 'a road bike' and len(seen['user']['components']) == 4
    shown = assembly.model(m['id'])
    wheel = next(p for p in shown['parts'] if p['id'] == 'front-wheel')
    assert wheel['purpose'] == 'Steers and rolls.' and wheel['notes_by'] == 'ai'
    assert 'AI-drafted' in shown['caption'] and 'not reviewed' in shown['validation']['review']
    assert next(p for p in shown['parts'] if p['id'] == 'rear-wheel')['purpose'].startswith('Not described')
    assert study_projects.model_hash(m['id']) == before, 'notes must not invalidate saved studies'
    with pytest.raises(si.StudyImportError, match='unreadable'):
        si.draft_notes(m, lambda s, u: 'I cannot help with that.')
    with pytest.raises(si.StudyImportError, match='No usable notes'):
        si.draft_notes(m, lambda s, u: json.dumps({'parts': {'nope': {'purpose': 'x'}}}))
    with pytest.raises(si.StudyImportError, match='only be drafted for imported'):
        si.draft_notes(assembly.model('heart'), fake)


def test_remove_only_imports(study_dir):
    m = si.import_model(sketchfab_like(), file_name='bike.glb')
    si.draft_notes(m, lambda s, u: json.dumps({'parts': {'frame': {'purpose': 'x'}}}))
    si.remove(m['id'])
    assert not list(study_dir.iterdir())
    with pytest.raises(ValueError):
        assembly.model(m['id'])
    with pytest.raises(si.StudyImportError, match='Only imported'):
        si.remove('heart')


def test_api_import_draft_and_remove(monkeypatch, study_dir):
    from dashboard import server
    from agent import core, provider
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'import-test')
    monkeypatch.setattr(provider, 'complete', lambda model, system, user, max_tokens=0: json.dumps(
        {'parts': {c['id']: {'purpose': 'Drafted.'} for c in json.loads(user)['components']}}))
    with TestClient(server.app) as c:
        assert c.post('/api/study/import', content=sketchfab_like()).status_code == 401
        c.headers['Authorization'] = 'Bearer import-test'
        assert c.post('/api/study/import', content=sketchfab_like(), headers={'Origin': 'https://evil.example'}).status_code == 403
        bad = c.post('/api/study/import?name=x.stl', content=b'solid cube')
        assert bad.status_code == 400 and 'not a glTF' in bad.json()['detail']
        r = c.post('/api/study/import?name=bike.glb&title=Road%20bike&category=Vehicles&detail=auto', content=sketchfab_like())
        assert r.status_code == 200, r.text
        mid = r.json()['id']
        assert r.json() == {'id': mid, 'title': 'Road bike', 'parts': 4}
        assert mid in core.TOOLS[[t['name'] for t in core.TOOLS].index('assembly_study')]['input_schema']['properties']['model']['enum']
        assert any(m['id'] == mid and m['category'] == 'Vehicles' for m in c.get('/api/study/models').json()['models'])
        assert c.get(f'/api/study/model/{mid}/asset').content == sketchfab_like()
        assert c.post(f'/api/study/model/{mid}/draft-notes', json={}).json() == {'drafted': 4, 'of': 4}
        assert c.get(f'/api/study/model/{mid}').json()['parts'][0]['purpose'] == 'Drafted.'
        assert c.post('/api/study/model/heart/draft-notes', json={}).status_code == 400
        assert c.delete('/api/study/model/heart').status_code == 400
        assert c.delete(f'/api/study/model/{mid}').json() == {'removed': mid}
        assert c.get(f'/api/study/model/{mid}').status_code == 404
        assert mid not in core.TOOLS[[t['name'] for t in core.TOOLS].index('assembly_study')]['input_schema']['properties']['model']['enum']
        monkeypatch.setattr(provider, 'complete', lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
        mid = c.post('/api/study/import?name=bike.glb', content=sketchfab_like()).json()['id']
        failed = c.post(f'/api/study/model/{mid}/draft-notes', json={})
        assert failed.status_code == 502 and 'Could not reach' in failed.json()['detail']
