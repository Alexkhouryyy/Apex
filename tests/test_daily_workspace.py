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
