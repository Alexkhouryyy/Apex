from types import SimpleNamespace
import pytest
import config
import main
from agent.handtrack import PinchIntent


def sample(gate, t, ratio, key=0, measure='3d', fist=False):
    detail = dict(id=key, ratio=ratio, measure=measure, threshold=.22, release=.32,
                  pinched=ratio is not None and ratio < .22, fist=fist)
    cursors, details = gate.filter([(.5,.5,detail['pinched'],False,key)], [detail], t)
    return cursors[0][2], details[0]['intent']


def test_resting_closed_hand_never_arms():
    g = PinchIntent()
    for t in [0,.05,.1,.2,1]:
        assert not sample(g,t,.1)[0]


def test_open_then_brief_closure_is_not_a_grab():
    g=PinchIntent()
    sample(g,0,.5)
    assert not sample(g,.05,.1)[0]
    assert not sample(g,.10,.1)[0]
    assert not sample(g,.15,.3)[0]
    assert not sample(g,.20,.1)[0]


def test_stable_closure_hysteresis_and_release():
    g=PinchIntent()
    for t,r in [(0,.5),(.05,.1),(.1,.1)]: assert not sample(g,t,r)[0]
    assert sample(g,.15,.1)[0]
    assert sample(g,.20,.28)[0]
    assert sample(g,.25,None)[0]
    assert not sample(g,.40,None)[0]
    assert not sample(g,.45,.1)[0]


def test_each_hand_arms_independently_and_lost_track_resets():
    g=PinchIntent()
    sample(g,0,.5,key=1)
    for t in [.05,.1,.15]:
        left=sample(g,t,.1,key=1)[0]
        assert not sample(g,t,.1,key=2)[0]
    assert left
    assert not sample(g,.5,.1,key=1)[0]


@pytest.mark.parametrize('measure,ratio,fist', [('2d',.1,False),('3d',float('nan'),False),('3d',.1,True)])
def test_unreliable_measurements_and_fists_never_grab(measure,ratio,fist):
    g=PinchIntent();sample(g,0,.5)
    for t in [.05,.1,.15,.2]: assert not sample(g,t,ratio,measure=measure,fist=fist)[0]


def test_held_card_takes_precedence_over_text_mode_microphone(monkeypatch):
    from agent import board
    monkeypatch.setattr(config,'BOARD_ENABLED',True)
    monkeypatch.setattr(board,'get_board',lambda:SimpleNamespace(hands_idle=lambda:(False,'Rocket is held')))
    out=main.make_gesture_handler('text')('pinch_hold','listen')
    assert 'Rocket is held' in out and 'no microphone' not in out
