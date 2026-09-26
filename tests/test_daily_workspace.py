"""Workspace edits use real board history, auth and hand arbitration."""
import pytest
from fastapi.testclient import TestClient
import config
from agent.board import Board, ARM_DWELL_SECONDS
from agent import board as board_mod


def test_paused_hands_cannot_grab_or_point_and_resume_needs_dwell():
    b = Board()
    c = b.add('card', 'Note', x=.5, y=.5)
    b.apply_hands([(.5, .5, True, False, 0)], now=1)
    b.set_hands_enabled(False)
    for t in (2, 3, 4):
        b.apply_hands([(.5, .5, True, False, 0)], now=t)
    assert not c.held_by
    assert b.pointed() is None
    assert b.hand_anchor() is None
    b.set_hands_enabled(True)
    b.apply_hands([(.5, .5, True, False, 0)], now=5)
    assert not c.held_by
    b.apply_hands([(.5, .5, True, False, 0)], now=5 + ARM_DWELL_SECONDS + .01)
    assert c.held_by
    with pytest.raises(ValueError, match='Release'):
        b.set_hands_enabled(False)
    assert b.hands_enabled


def test_paused_board_blocks_voice_and_other_gesture_dispatch(monkeypatch):
    from agent.handtrack import HandTracker
    b = Board()
    b.set_hands_enabled(False)
    monkeypatch.setattr(config, 'BOARD_ENABLED', True)
    monkeypatch.setattr(board_mod, 'get_board', lambda: b)
    tracker = HandTracker.__new__(HandTracker)
    for gesture in ('swipe_up', 'swipe_down', 'wave', 'pinch_hold'):
        assert tracker._board_blocks(gesture) == 'hand controls are paused'


def test_workspace_routes_require_auth_validate_and_undo(monkeypatch):
    from dashboard import server
    b = Board()
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'workspace-test-token')
    monkeypatch.setattr(board_mod, 'get_board', lambda: b)
    with TestClient(server.app) as client:
        route = '/api/board/workspace'
        assert client.post(route, json={'action': 'note', 'title': 'No auth'}).status_code == 401
        client.headers['Authorization'] = 'Bearer workspace-test-token'
        assert client.post(route, json={'action': 'note', 'title': 'Bad origin'}, headers={'Origin': 'https://other.example'}).status_code == 403
        c = client.post(route, json={'action': 'note', 'title': 'Idea', 'body': 'Research'}).json()['card']
        assert b.selection()['id'] == c['id']
        assert client.post(route, json={'action': 'transform', 'id': c['id'], 'changes': {'x': .7, 'y': .6}}).status_code == 200
        assert b.cards()[0]['x'] == .7
        assert client.post(route, json={'action': 'undo'}).status_code == 200
        assert b.cards()[0]['x'] == .5
        client.post(route, json={'action': 'redo'})
        assert b.cards()[0]['x'] == .7
        for payload in ({'action': 'hands', 'enabled': 'false'}, {'action': 'note', 'title': ''},
                        {'action': 'transform', 'id': c['id'], 'changes': {'x': 2}}, {'action': 'clear'}):
            assert client.post(route, json=payload).status_code == 400
        assert client.post(route, json={'action': 'hands', 'enabled': False}).json() == {'hands_enabled': False}


def test_note_link_edits_conflicts_history_and_restart(tmp_path, monkeypatch):
    from agent import longterm
    from agent.board import ContentConflict
    monkeypatch.setattr(longterm, 'DB_PATH', str(tmp_path / 'workspace.db'))
    board_mod.init_db()
    b = Board(); b.persist = True
    c = b.save_text('link', 'Apex', 'Read the roadmap', 'https://github.com/Alexkhouryyy/Apex')
    b.transform(c['id'], x=.7)
    edited = b.save_text('link', 'Apex sources', 'Review progress', c['src'], c['id'], c['content_revision'])
    assert edited['x'] == .7, 'moving must not conflict with content editing'
    with pytest.raises(ContentConflict):
        b.save_text('link', 'Stale edit', '', c['src'], c['id'], c['content_revision'])
    fresh = Board(); fresh.restore()
    assert fresh.cards()[0]['title'] == 'Apex sources'
    assert fresh.cards()[0]['src'] == c['src']
    b.undo()
    fresh.restore(); assert fresh.cards()[0]['title'] == 'Apex'
    assert fresh.cards()[0]['x'] == .7, 'content undo preserves placement'
    b.redo(); fresh.restore(); assert fresh.cards()[0]['title'] == 'Apex sources'
    b.remove(c['id'])
    with pytest.raises(ContentConflict, match='removed'):
        b.save_text('link', 'Lost', '', c['src'], c['id'], edited['content_revision'])


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,hi', 'file:///etc/passwd',
                                '//example.com', 'https://user:pass@example.com', 'https://@example.com',
                                'https://example.com/\nattack', 'https://example.com:bad',
                                'https://example.com\\@other.com', 'https://'])
def test_unsafe_link_addresses_rejected(url):
    b = Board()
    with pytest.raises(ValueError):
        b.save_text('link', 'Unsafe', src=url)
    assert b.cards() == []


def test_text_save_failure_keeps_memory_history_and_existing_items(tmp_path, monkeypatch):
    from agent import longterm
    b = Board(max_cards=1)
    c = b.save_text('card', 'Original', 'Keep me')
    with pytest.raises(ValueError, match='full'):
        b.save_text('card', 'Another')
    b.persist = True
    def broken():
        raise OSError('Disk unavailable')
    monkeypatch.setattr(longterm, '_conn', broken)
    with pytest.raises(RuntimeError, match='Could not save'):
        b.save_text('card', 'New', '', '', c['id'], c['content_revision'])
    assert b.cards()[0] == c
    assert len(b._undo) == 1
    empty = Board(); empty.persist = True
    with pytest.raises(RuntimeError):
        empty.save_text('card', 'New')
    assert empty.cards() == [] and empty._undo == []


def test_content_routes_unicode_conflicts_and_edit_validation(monkeypatch):
    from dashboard import server
    b = Board()
    monkeypatch.setattr(config, 'DASHBOARD_TOKEN', 'workspace-test-token')
    monkeypatch.setattr(board_mod, 'get_board', lambda: b)
    with TestClient(server.app) as client:
        route = '/api/board/workspace'
        client.headers['Authorization'] = 'Bearer workspace-test-token'
        original = client.post(route, json={'action':'note','title':'دراسة','body':'م' * 600})
        assert original.status_code == 200
        c = original.json()['card']
        edit = dict(action='edit_content', kind='card', id=c['id'], title='Updated', expected_revision=c['content_revision'])
        assert client.post(route, json=edit).status_code == 200
        assert client.post(route, json=edit).status_code == 409
        assert client.post(route, json={**edit, 'id':None}).status_code == 400
        assert client.post(route, json={'action':'note','title':'x','body':'x'*20000}).status_code == 400
        assert client.post(route, json={'action':'link','title':'Docs','src':'https://example.com'}).status_code == 200
        c = b.cards()[0]; b._cards[0].held_by = [0]
        with pytest.raises(ValueError, match='Release'):
            b.save_text('card', 'Busy', '', '', c['id'], c['content_revision'])
