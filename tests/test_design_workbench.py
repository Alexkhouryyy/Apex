"""A real measured build must survive edit, history and checked file export."""
import copy

import numpy as np
import pytest
from fastapi.testclient import TestClient

import config
from agent import assets, board, board_parts, build3d, design_workbench as design, forge, props


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'props'; root.mkdir()
    monkeypatch.setattr(props, 'props_root', lambda: root)
    b = board.Board(); b.persist = False
    monkeypatch.setattr(board, '_board', b)
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'design-test')
    return b, root


def make(**overrides):
    return design.create(dict(title='Measured block', shape='box', size_mm=[40, 12, 30], **overrides))


def request(data, **overrides):
    return {'id': data['card']['id'], 'expected_src': data['source'], 'workspace': data['workspace'], **overrides}


def test_mm_edit_is_a_new_geometry_version_and_undo_restores_the_file(workspace):
    b, root = workspace
    data = make()
    old = props.resolve(data['source']).read_bytes()
    assert data['extent_mm'] == pytest.approx([40, 12, 30])
    result = design.edit(request(data, part=0, changes={'size_mm': [42, 14, 32]}))
    assert result['version'] == 2 and result['parts'][0]['size_mm'] == [42, 14, 32]
    assert props.resolve(data['source']).read_bytes() == old
    assert b.selection()['selected_part']['name'] == 'Measured block'
    b.undo(); assert b.cards()[0]['src'] == data['source']
    b.redo(); assert b.cards()[0]['src'] == result['source']


def test_enclosure_parts_are_independent_and_scoped_edits_leave_siblings_alone(workspace):
    data = design.create(dict(title='Enclosure', shape='enclosure', size_mm=[40, 12, 40]))
    assert [p['name'] for p in data['parts']] == ['Base', 'Wall', 'Lid']
    original = copy.deepcopy(data['parts'])
    result = design.edit(request(data, part=2, changes={'size_mm': [42, 2, 42], 'rotation_deg': [0, 0, 15]}))
    assert result['parts'][:2] == original[:2]
    assert result['parts'][2]['size_mm'] == [42, 2, 42]


def test_stale_edit_is_409_material_and_does_not_write_over_current_geometry(workspace):
    data = make()
    current = design.edit(request(data, part=0, changes={'name': 'Renamed'}))
    with pytest.raises(board.ContentConflict, match='draft is preserved'):
        design.edit(request(data, part=0, changes={'size_mm': [99, 99, 99]}))
    assert design.inspect(data['card']['id'])['source'] == current['source']
    assert len(assets.load('measured-block')['versions']) == 2


def test_edit_after_undo_branches_from_the_shown_source_not_the_newest_file(workspace):
    b, _ = workspace
    data = make()
    design.edit(request(data, part=0, changes={'size_mm': [42, 14, 32]}))
    b.undo()
    changed = design.edit(request(data, part=0, changes={'name': 'From original'}))
    assert changed['version'] == 3
    record = assets.load('measured-block')['versions'][-1]
    assert record['parent'] == 1
    assert record['command']['parts'][0]['size'] == pytest.approx([4, 1.2, 3])


@pytest.mark.parametrize('value', [[True, 3, 3], [float('nan'), 2, 2], [float('inf'), 2, 2], [0, 2, 2], [-1, 2, 2], [3, 4]])
def test_invalid_physical_measurements_leave_history_and_source_unchanged(workspace, value):
    data = make()
    with pytest.raises(ValueError):
        design.edit(request(data, part=0, changes={'size_mm': value}))
    assert len(assets.load('measured-block')['versions']) == 1
    assert design.inspect(data['card']['id'])['source'] == data['source']


def test_active_gesture_cannot_be_overwritten_by_inspector(workspace, monkeypatch):
    data = make()
    monkeypatch.setattr(workspace[0], 'hands_idle', lambda: (False, 'holding a component'))
    with pytest.raises(board.ContentConflict, match='Release'):
        design.edit(request(data, part=0, changes={'name': 'Unexpected'}))
    assert len(assets.load('measured-block')['versions']) == 1


def test_changed_workspace_is_refused_before_creating_files(workspace):
    b, root = workspace
    b.workspace_id = 'project-a'; b.workspace_epoch = 'epoch-a'
    with pytest.raises(ValueError, match='workspace changed'):
        design.create(dict(title='Wrong project', shape='box', size_mm=[20, 20, 20], workspace={'id':'project-b', 'epoch':'epoch-b'}))
    assert not list(root.rglob('*.glb'))


def test_export_uses_selected_source_and_converts_y_up_to_print_z_up(workspace):
    data = make()
    build3d.build('Measured block', [dict(shape='box', size=[9, 9, 9])])
    result = design.fabrication(request(data, part=0, format='3mf'), export=True)
    assert result['ok'] and result['file'].endswith('.3mf')
    path = props.resolve(result['file'], exts=props.FABRICATION_EXTS)
    mesh = forge.read_any(path)
    low, high = mesh.bounds_mm()
    np.testing.assert_allclose(high-low, [40, 30, 12], atol=.001)
    assert low[2] == pytest.approx(0)
    meta = assets.load('measured-block')
    assert meta['versions'][-1]['parent'] == 1
    assert design.inspect(data['card']['id'])['source'] == data['source']


def test_unknown_check_never_creates_an_export_or_version(workspace, monkeypatch):
    data = make()
    monkeypatch.setattr(forge, 'validate', lambda *a, **k: forge.Report([forge.Finding('thickness', forge.UNKNOWN, 'Not measured')]))
    result = design.fabrication(request(data, format='3mf'), export=True)
    assert not result['ok'] and 'file' not in result
    assert len(assets.load('measured-block')['versions']) == 1
    assert not list(workspace[1].rglob('*.3mf'))


def test_geometry_change_during_validation_cannot_publish_a_stale_export(workspace, monkeypatch):
    data = make(); validate = forge.validate
    def changed(*a, **kw):
        design.edit(request(data, part=0, changes={'size_mm':[50, 20, 40]}))
        return validate(*a, **kw)
    monkeypatch.setattr(forge, 'validate', changed)
    with pytest.raises(board.ContentConflict):
        design.fabrication(request(data, format='3mf'), export=True)
    assert not list(workspace[1].rglob('*.3mf'))


def test_owner_origin_and_expected_revision_are_enforced_by_http(workspace):
    from dashboard.server import app
    data = make()
    client = TestClient(app)
    endpoint = '/api/board/design'
    headers = {'Authorization':'Bearer design-test'}
    assert client.get(endpoint+'/'+data['card']['id']).status_code == 401
    assert client.get(endpoint+'/'+data['card']['id'], headers=headers).status_code == 200
    assert client.post(endpoint, headers={**headers,'Origin':'https://foreign.example'}, json=request(data, action='edit', part=0, changes={'name':'No'})).status_code == 403
    result = client.post(endpoint, headers=headers, json=request(data, action='edit', part=0, changes={'size_mm':[42, 14, 32]}))
    assert result.status_code == 200, result.text
    assert client.post(endpoint, headers=headers, json=request(data, action='edit', part=0, changes={'name':'Stale'})).status_code == 409
    assert client.post(endpoint, headers=headers, content='x'*5000).status_code == 400
