"""Workspace boundaries must survive stale windows, late writes and restart."""
import pytest
from fastapi.testclient import TestClient
import config
from agent import board, board_workspaces as spaces, longterm, study_input


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(longterm, 'DB_PATH', str(tmp_path / 'workspaces.db'))
    monkeypatch.setattr(board, '_board', None)
    monkeypatch.setattr(spaces, '_boards', {})
    monkeypatch.setattr(study_input, '_lease', None)
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'spaces-test')
    board.init_db()
    return tmp_path


def test_legacy_migration_is_once_and_restart_restores_active_board(isolated):
    legacy = board.Board(); legacy.persist = True
    legacy.save_text('card', 'Existing work', 'Keep this')
    first = board.get_board()
    assert first.workspace_name == 'My workspace'
    assert first.cards()[0]['title'] == 'Existing work'
    original_epoch = first.workspace_epoch
    other = spaces.create('Sky Light', False, first.workspace_context())
    spaces.switch(other['id'], first.workspace_context())
    board.get_board().save_text('link', 'Reference', src='https://example.com')
    board._board = None
    restored = board.get_board()
    assert restored.workspace_id == other['id']
    assert restored.cards()[0]['title'] == 'Reference'
    spaces.switch('default', restored.workspace_context())
    assert len(board.get_board().cards()) == 1
    assert board.get_board().workspace_epoch != original_epoch
    with longterm._conn() as db:
        assert db.execute('SELECT COUNT(*) FROM board_cards').fetchone()[0] == 1


def test_isolated_history_copies_and_late_writes(isolated):
    first = board.get_board()
    card = first.save_text('card', 'Apex', 'Original')
    first.transform(card['id'], x=.7)
    first.add('model', 'Motor', src='created/motor.glb')
    copy = spaces.create('Engineering', True, first.workspace_context())
    old_context = first.workspace_context()
    spaces.switch(copy['id'], old_context)
    second = board.get_board()
    copied = second.cards()
    assert {c['id'] for c in copied}.isdisjoint(c['id'] for c in first.cards())
    assert copied[0]['x'] == .7 and copied[1]['src'] == 'created/motor.glb'
    assert second.undo() is None
    # A background operation that captured the previous Board stays in that board.
    first.save_text('card', 'Late update', '', '', card['id'], card['content_revision'])
    assert second.cards()[0]['title'] == 'Apex'
    second.save_text('card', 'Only here')
    spaces.switch('default', second.workspace_context())
    assert len(board.get_board().cards()) == 2
    first.undo(); assert first.cards()[0]['title'] == 'Apex'
    assert len(second.cards()) == 3
    with pytest.raises(spaces.Conflict):
        spaces.check_context(first, old_context)


def test_holds_storage_failure_and_invalid_target_do_not_replace_board(isolated, monkeypatch):
    first = board.get_board()
    c = first.save_text('card', 'Stay here')
    other = spaces.create('Other', False, first.workspace_context())
    first._cards[0].held_by = [1]
    with pytest.raises(ValueError, match='Release'):
        spaces.switch(other['id'], first.workspace_context())
    assert board.get_board() is first
    first._cards[0].held_by = []
    def fail(**kwargs): raise RuntimeError('Disk failed')
    monkeypatch.setattr(first, '_persist_all', fail)
    with pytest.raises(RuntimeError): spaces.switch(other['id'], first.workspace_context())
    assert board.get_board() is first and first.cards()[0]['id'] == c['id']
    with pytest.raises(ValueError, match='not found'): spaces.switch('missing', first.workspace_context())
    with longterm._conn() as db:
        assert db.execute("SELECT value FROM workspace_settings WHERE key='active'").fetchone()[0] == 'default'


def test_context_auth_origin_and_stale_window_writes(isolated):
    from dashboard.server import app
    with TestClient(app) as client:
        assert client.get('/api/board/workspaces').status_code == 401
        client.headers['Authorization'] = 'Bearer spaces-test'
        ctx = client.get('/api/board/workspaces').json()['active']
        create = dict(action='create', name='Apex', context=ctx)
        assert client.post('/api/board/workspaces', json=create, headers={'Origin':'https://other.example'}).status_code == 403
        new = client.post('/api/board/workspaces', json=create).json()['workspace']
        switched = client.post('/api/board/workspaces', json=dict(action='switch',id=new['id'],context=ctx))
        assert switched.status_code == 200
        assert client.post('/api/board/workspace',json=dict(action='note',title='Wrong board',workspace=ctx)).status_code == 409
        assert client.post('/api/board/workspace',json=dict(action='undo',workspace=ctx)).status_code == 409
        assert client.post('/api/board/workspaces',json=dict(action='switch',id='default',context=ctx)).status_code == 409
        current = switched.json()['active']
        assert client.post('/api/board/workspace',json=dict(action='note',title='Right board',workspace=current)).status_code == 200
        assert client.post('/api/board/workspaces',json=dict(action='create',name='',context=current)).status_code == 400
        assert client.post('/api/board/workspace',content='[').status_code == 400
        assert len(board.get_board().cards()) == 1


def test_workspace_backup_restores_all_boards(isolated, monkeypatch):
    from pathlib import Path
    from scripts.backup_brain import backup
    first = board.get_board(); first.save_text('card', 'First')
    new = spaces.create('Second', False, first.workspace_context())
    spaces.switch(new['id'], first.workspace_context())
    board.get_board().save_text('card', 'Second')
    saved = isolated / 'backup.db'
    info = backup(Path(longterm.DB_PATH), saved)
    assert info['counts']['board_workspaces'] == 2 and info['counts']['workspace_cards'] == 2
    monkeypatch.setattr(longterm, 'DB_PATH', str(saved)); board._board = None
    restored = board.get_board(); assert restored.cards()[0]['title'] == 'Second'
    spaces.switch('default', restored.workspace_context())
    assert board.get_board().cards()[0]['title'] == 'First'
