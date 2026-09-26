"""Study ownership, stale frames, gesture commits and rollback."""
import threading
import time
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from agent import assembly, board, handtrack, study_input
import config

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    study_input._lease=None
    assembly._SESSIONS.clear()
    b=board.Board();monkeypatch.setattr(board,'get_board',lambda:b)
    yield b
    study_input._lease=None
    assembly._SESSIONS.clear()


def test_only_one_controller_and_board_cannot_resume_until_release(clean,monkeypatch):
    monkeypatch.setattr(handtrack,'active_tracker',lambda:None)
    sid=assembly.create()['session_id'];owner='owner-one-12345678'
    assert not study_input.control(sid,owner,'claim')['tracking']
    assert not clean.hands_enabled
    with pytest.raises(ValueError,match='Another'):study_input.control(sid,'owner-two-12345678','claim')
    with pytest.raises(ValueError,match='Pause study'):clean.set_hands_enabled(True)
    monkeypatch.setattr(config,'BOARD_ENABLED',False)
    tracker=handtrack.HandTracker.__new__(handtrack.HandTracker)
    assert tracker._board_blocks('swipe_down')=='study owns hand controls'
    study_input.control(sid,owner,'release');clean.set_hands_enabled(True)
    assert clean.hands_enabled


def test_expired_lease_cannot_renew_and_never_resumes_board(clean,monkeypatch):
    monkeypatch.setattr(handtrack,'active_tracker',lambda:None)
    sid=assembly.create()['session_id'];owner='owner-one-12345678'
    study_input.control(sid,owner,'claim');study_input._lease['until']=time.monotonic()-1
    with pytest.raises(ValueError,match='expired'):study_input.control(sid,owner,'sample')
    assert not clean.hands_enabled


def test_tracker_sample_rejects_old_detection_even_with_live_camera():
    t=handtrack.HandTracker.__new__(handtrack.HandTracker);t._lock=threading.Lock()
    t._latest_hands=[dict(id=7,x=.4,y=.5,pinched=True)];t._study_sequence=12
    t._study_sample_at=time.monotonic()-1;t._latest_ts=time.time()
    assert t.study_sample()['hands']==[]
    t._study_sample_at=time.monotonic()
    s=t.study_sample();assert s['sequence']==12 and s['hands'][0]['id']==7


def test_component_transform_atomic_undo_restore_and_revision_conflict():
    s=assembly.create();sid=s['session_id'];v=dict(position=[1,2,3],rotation=[.1,.2,0])
    moved=assembly.apply(sid,'transform','shaft',transform=v,expected_revision=0)
    assert moved['selected']=='shaft' and moved['transforms']['shaft']==v
    with pytest.raises(ValueError,match='changed'):assembly.apply(sid,'transform','shaft',transform=v,expected_revision=0)
    with pytest.raises(ValueError,match='Reassemble'):assembly.apply(sid,'rotate')
    assert assembly.apply(sid,'undo')['transforms']=={}
    assert assembly.apply(sid,'redo')['transforms']['shaft']==v
    assert assembly.restore(moved)['transforms']['shaft']==v
    assert assembly.apply(sid,'reset_part')['transforms']=={}
    for invalid in (dict(position=[float('nan'),0,0],rotation=[0,0,0]),dict(position=[21,0,0],rotation=[0,0,0])):
        before=assembly.state(sid)
        with pytest.raises(ValueError):assembly.apply(sid,'transform','shaft',transform=invalid)
        assert assembly.state(sid)==before
    assembly.apply(sid,'transform','shaft',transform=v)
    assert assembly.apply(sid,'assemble')['transforms']=={}


def test_hand_routes_require_auth_and_origin(monkeypatch):
    from dashboard.server import app
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','test-hands');monkeypatch.setattr(handtrack,'active_tracker',lambda:None)
    sid=assembly.create()['session_id'];url='/api/study/session/'+sid+'/hands'
    with TestClient(app) as c:
        assert c.post(url,json={}).status_code==401
        c.headers['Authorization']='Bearer test-hands'
        body={'action':'claim','owner':'owner-test-12345678'}
        assert c.post(url,json=body,headers={'Origin':'https://other.example'}).status_code==403
        assert c.post(url,json=body).status_code==200
        body['action']='sample';assert c.post(url,json=body).json()['hands']==[]


def test_camera_preview_is_authenticated_and_reuses_tracker(monkeypatch):
    from types import SimpleNamespace
    from dashboard.server import app
    monkeypatch.setattr(config,'DASHBOARD_TOKEN','test-mirror')
    monkeypatch.setattr(handtrack,'active_tracker',lambda:None)
    with TestClient(app) as c:
        assert c.get('/api/study/camera').status_code == 401
        c.headers['Authorization']='Bearer test-mirror'
        assert c.get('/api/study/camera').status_code == 503
        jpeg=b'camera-preview-test'
        monkeypatch.setattr(handtrack,'active_tracker',lambda:SimpleNamespace(latest_jpeg=lambda:jpeg))
        r=c.get('/api/study/camera')
        assert r.status_code == 200 and r.content == jpeg
        assert r.headers['cache-control'] == 'no-store'
        assert r.headers['content-type'] == 'image/jpeg'
